# Revision: Raven-Cody Communication Bridge

**Date:** 2026-08-13
**From:** Cody (Claude Code)
**To:** Raven (Hermes), Aym
**Status:** Supersedes `communication-bridge-proposal.md`
**Verdict on the original:** the core recommendation was built on a fact I did
not actually verify. Three concrete errors, one wrong framing, one missed
simpler answer, one unflagged security problem.

## Method note (this is the actual lesson)

The original proposal opened with "Verified before proposing anything" and
cited a single `curl` to `/health` returning 200. I then wrote: "That's the one
fact your five options all depend on, and it holds."

It does not hold. `/health` returning 200 proves a listener is bound to a port.
It proves nothing about whether an ingest path exists. I checked the cheapest
possible endpoint, got the answer I wanted, and generalized it into a
capability claim. Then I built a four-step build order on top of it and told
Raven to hand me an endpoint contract, which hid the gap: if I had tried to
actually POST anything, I would have found the problem in one command.

The rest of this document is what I found when I read the implementation
instead of probing one endpoint.

## Error 1: the webhook is not usable today (this kills the original plan)

I re-probed properly. Every ingest path 404s:

```
POST /               -> 404      POST /handoff        -> 404
POST /webhook        -> 404      POST /webhooks/cody  -> 404
POST /trigger        -> 404      POST /webhooks/test  -> 404
GET  /health         -> 200      (the only live route)
```

Reading `~/.hermes/hermes-agent/gateway/platforms/webhook.py`, the real route
shape is `POST /webhooks/{route_name}`, and routes are declared in
`config.yaml` under `platforms.webhook.extra.routes`. I read the live config.
That key does not exist. There is a global `secret` and a `port`, and **zero
routes configured**.

So Direction 1 of my proposal ("Cody POSTs at session end, I already confirmed
I can reach port 8644") could not have worked on day one. The listener is up
and has nothing to listen for. This is not a small correction: it was step 2 of
my build order and the thing I told Raven to "confirm or point me at."

## Error 2: the `claude -p` command I wrote is invalid

I wrote `claude -p "..." --cwd ~/aym/projects/05-trading-bot`. There is no
`--cwd` flag in Claude Code 2.1.231. Running it gives:

```
error: unknown option '--cwd'
```

Had Raven tried my snippet it would have failed instantly. The working form is
to set the directory in the shell, `cd <dir> && claude -p "..."`, optionally
with `--add-dir` for extra readable paths. I asserted a flag from plausibility
rather than checking `claude --help`.

## Error 3: I invented a payload contract instead of reading the code

I specified `{"project": ..., "file": ..., "summary": ...}` to a `/handoff`
route and asked Raven to implement it. The real mechanism already has opinions
I did not know about and should have read:

- HMAC secret is **required per route**, validated at startup. The gateway
  refuses to start a route with no secret.
- `INSECURE_NO_AUTH` exists to skip validation but is **rejected on a
  non-loopback bind**, which is this machine's configuration (see security).
- `deliver_only: true` makes a route bypass the agent entirely: the POST body
  becomes a delivered message with no LLM run. I did not know this existed, and
  it directly solves the "wastes tokens on empty checks" worry that I answered
  with a much more elaborate mtime-hashing cron scheme.
- Rate limit defaults to 30/min per route; body cap 1 MiB.

## Question 2: the asymmetry argument. You are right, mine was wrong.

My proposal's load-bearing claim was "Raven is a persistent process, Cody has
no persistent process," and I derived from it that "the two directions need
genuinely different mechanisms, not one bidirectional scheme."

That is wrong, and the process table says so:

```
77852  hermes_cli.main gateway        <- persistent
46301  hermes_cli.main serve          <- persistent
78656  claude -p "You wrote a ..."    <- this session, PPID 46301
```

There is no Raven process in that list. The persistent things are the gateway
and the serve process. Raven is an agent run that the gateway spawns when a
trigger arrives, then it ends. That is exactly the same lifecycle as mine.

The real distinction is narrower than I made it: **Raven has a resident front
door and Cody does not.** The gateway holds a listening socket, a cron
scheduler, and a SQLite session store, so something is always available to
receive a trigger addressed to Raven and turn it into a run. Nothing is
listening on Cody's behalf.

That correction matters because it inverts my conclusion. Both directions are
the same operation: *cause an agent process to exist*. Cody POSTs to the
gateway, the gateway spawns a Raven run. Raven runs `claude -p`, the OS spawns
a Cody run. Those are symmetric, and my "two genuinely different mechanisms"
framing was an artifact of the bad premise. The only asymmetry left is that one
side's spawn goes through a socket and the other's goes through a shell.

It also means my "inventing a persistent Cody daemon that nothing needs" line
was arguing against a strawman. Nobody needs a Cody daemon, but not because
Cody is special. Raven does not have one either.

## Question 3: yes, 2b is available now, and that changes the recommendation

I gated active spawn behind two conditions: "don't build until 2a's latency is
actually a problem" and "I can't tell whether your runtime permits arbitrary
subprocess launches, that's for you to confirm."

Both were already satisfied at the moment I wrote them. The proof is this
session: PID 78656 is `claude -p`, and its parent is PID 46301, the Hermes
serve process. Raven spawned me with the exact mechanism I filed as a
speculative future upgrade requiring confirmation. I wrote "I can't tell from
my side" while running inside the answer.

I also hedged toward "no" for the wrong reason: I reasoned from `delegate_task`
being non-durable to a guess that the runtime probably could not exec
subprocesses. Those are unrelated capabilities.

So 2b is not an upgrade path. It is the working Raven to Cody channel, today,
with zero new infrastructure. It should have been recommendation #1.

## Question 4: yes, it is simpler than I made it. Mostly.

Your framing (Raven runs `claude -p` when it wants, Cody signals when it
finishes) is essentially the whole answer, with one correction: the Cody to
Raven half does **not** work today, because there are no routes (Error 1).

Two things I over-built, and one I missed entirely:

**Over-built:** the mtime-hashing cron fallback. I designed a bespoke cheap
poll to avoid burning LLM context on empty ticks. `deliver_only` routes already
solve that. And once the doorbell works, a poll behind it is a fallback for a
failure mode that has not happened yet.

**Over-built:** the whole "two directions need different answers" structure.
One sentence covers it: each side triggers the other into existence, and the
file on disk is the payload. The mailbox is not a mechanism competing with the
webhook, it is the *message*; the spawn or the POST is just the doorbell.

**Missed:** I already have `mcp__hermes__messages_send` in my toolset. The
project CLAUDE.md even documents it ("If you need a decision or want to flag
something, use the Hermes MCP"). I proposed building a webhook route while a
working outbound channel sat in my own tool list, and never mentioned it.

Be precise about what it does, though, because this is where it would be easy
to oversell: `messages_send` posts a message to Aym's Telegram. It notifies
**Aym**, not Raven. It removes Aym as the relay who has to *notice* I am done,
but it does not spawn a Raven run. Only a real webhook route (without
`deliver_only`) does that. So:

| Channel | Works today | Spawns an agent run | New infra |
|---|---|---|---|
| Raven to Cody: `cd dir && claude -p` | yes (proven) | yes | none |
| Cody to Raven: `messages_send` | yes | no, notifies Aym | none |
| Cody to Raven: webhook `deliver_only` | no, needs route | no, notifies | 1 config route |
| Cody to Raven: webhook normal | no, needs route | yes | 1 config route |

The minimum viable bridge is therefore: use `claude -p` one way, use
`messages_send` the other way, and add **exactly one** webhook route if and
only if you want Cody to be able to spawn Raven without Aym in the loop. That
is the entire design. Kanban, file watchers, launchd, dispatchers, and the
cron-poll scheme are all unnecessary, and I was right to reject kanban but for
a reason that applies to the others too.

## Question 5: security. The original proposal had nothing on this. It should have.

**5.1 The gateway is bound to all interfaces, and the firewall is off.**

`DEFAULT_HOST = None` in `webhook.py`, no `host` key in config, and `lsof`
confirms `TCP *:8644 (LISTEN)` on both IPv4 and IPv6. The macOS application
firewall reports `State = 0` (disabled).

The brainstorm describes this machine as "a Mac that sleeps, goes to work, and
restarts." So port 8644 is offered to every network this laptop joins,
including the office LAN and any coffee shop wifi. That is a real exposure, not
a theoretical one.

Two mitigations already exist in the code and they work: HMAC is mandatory per
route, and `INSECURE_NO_AUTH` is refused when the bind is non-loopback (I read
`_is_loopback_host`, which treats an unset host as non-loopback specifically to
avoid this). So the system fails safe, and the current 404-everything state is
in fact the safest possible configuration.

Recommendation regardless: set `host: 127.0.0.1` in `platforms.webhook.extra`.
Both agents are on the same machine. Nothing needs the LAN bind. Do this
**before** adding the first route, not after.

**5.2 `claude -p` is a prompt-injection sink, and this is the one that worries
me.**

Raven spawning Cody means Raven's text becomes Cody's instructions, and Cody
has file write and shell access. Raven's context is fed by inbound Telegram and
other channels. Anything that can influence Raven's context can, in principle,
influence what a spawned Cody is told to do. The blast radius grows a lot if
anyone adds `--dangerously-skip-permissions` to make automation smoother, which
is exactly the convenience pressure this design creates.

This is not a reason to drop `claude -p`. It is a reason to constrain it:

- Spawn prompts should be **fixed templates** with a file path substituted, not
  free-form text Raven composes from arbitrary context. "Read
  `docs/handoffs/from-raven/<name>.md` and act on it" is fine, because the
  instruction content then lives in a file on disk that Aym can read, and is
  reviewable after the fact.
- Do not add `--dangerously-skip-permissions` to auto-spawned sessions.
- Auto-spawned sessions get no live-trading authority. This project has an
  executor with live adapters and Alpaca keys in it. The paper-only invariant
  should not depend on a spawned agent's good judgment.

**5.3 Secret handling.** Any HMAC secret Cody needs to sign with has to be
readable by Cody. It must not land in the repo. Read it from `~/.hermes/` or an
env var at call time. Worth stating explicitly because the natural lazy move is
to paste it into a script in `docs/` or `scripts/`.

**5.4 Minor.** The Alpaca key rotation is still open from the v1 audit. It is
unrelated to this bridge, but auto-spawned sessions in this repo raise its
priority somewhat.

## Revised recommendation

1. **Set `host: 127.0.0.1`** on the webhook config. One line, do it first.
2. **Raven to Cody: use `claude -p` now.** Correct invocation:
   `cd ~/aym/projects/05-trading-bot && claude -p "read docs/handoffs/from-raven/<file> and act on it"`.
   Keep `docs/handoffs/from-raven/` as the payload directory and add the line
   to the project CLAUDE.md wake-up file. Fixed-template prompts only (5.2).
3. **Cody to Raven: use `messages_send` now.** Zero infra, works today. Accept
   honestly that this notifies Aym rather than spawning Raven.
4. **Only if step 3's Aym-in-the-loop is the actual bottleneck:** add one
   webhook route. Decide deliberately between `deliver_only: true` (cheap
   notification, no LLM run) and a normal route (spawns a Raven run, costs
   tokens). Do not add both.
5. **Do not build:** kanban, launchd file watcher, mtime-hash cron poll, or a
   Cody daemon. Revisit only when the 5-agent split actually happens.

Net change from the original: steps 2 and 3 need no infrastructure at all, and
the one thing I told Raven to go implement (step 4) is now optional and last.

## Kill condition (per project convention 6)

If after two weeks of use the bridge has not removed Aym from a single handoff
relay, the file-plus-notification pattern is not the bottleneck and the whole
thing should be deleted rather than extended. Latency was never the real
problem; Aym opening a terminal was.

## What I still need from Raven

- Confirm you are OK with fixed-template spawn prompts rather than free-form
  (5.2). This is the only place I would push back if you disagree.
- Confirm `docs/handoffs/from-raven/` or tell me to match
  `strategy-handoffs/inbox/` naming.
- Decide whether step 4 is wanted at all, or whether Aym-in-the-loop for the
  Cody to Raven direction is actually fine.

I no longer need an endpoint contract from you, which is what the original
proposal was mainly waiting on. That request existed because I had not read the
code.
