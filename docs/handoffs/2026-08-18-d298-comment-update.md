# Handoff: D-298 referenced in the skip-classification comment

**From:** Cody, 2026-08-18
**Acting on:** `docs/handoffs/from-raven/2026-08-18-update-comment-d298.md`
**Scope:** comment-only. One file. No logic change.

## What changed

`agents/forge_shadow_eval.py`, the comment block above
`'no_recent_liquidation'` / `'liquidation_below_second_lock_min'`
(now lines 412-419).

Before, the block ended with "so this is cheap to reverse - but it needs a
D-number if it is." That D-number now exists, so the comment read as an open
dispute when it is settled.

After: the block names **D-298** as the ruling, keeps the whole code-path
explanation intact, and keeps the measured-consequence line (zero rows either
way as of today).

One wording deviation from the instruction, deliberate: Raven's text was
"D-298 ruled GENUINE, agreeing with the code-path argument below." I moved the
D-298 sentence to the TOP of the dispute paragraph so that "below" points at
the `window.ok` / `liquidation_feed_empty` explanation that follows it. Written
in place at the end of the paragraph, "below" would have pointed at nothing.
Same claim, same D-number, just ordered so the cross-reference is true.

## What did NOT change

- Both classifications are still `(GENUINE, '')`. Untouched.
- No other key, no other file, no logic anywhere.
- No processes restarted, no feeds killed, nothing committed.

## Verification

```
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_forge_shadow_eval.py -q
44 passed in 0.12s
```

I checked D-298 exists before editing (convention 24): DECISIONS.md line 1882,
"no_recent_liquidation and liquidation_below_second_lock_min stay GENUINE
(RAVEN RULING)". It is a real decision, not a cited number.

`engine.concurrency who` showed no open checkout on `forge_shadow_eval.py`
before the edit (the only active checkout was another session's handoff file).

## Next steps for Raven

- The tree is left dirty for review, as instructed. Nothing staged.
- CLAUDE.md's "Open disagreement with Raven, unresolved" section is now stale
  in the same way the comment was. I updated it to point at D-298. That file is
  untracked and never reaches GitHub.
- The other four open rulings in CLAUDE.md's "What's next" list are untouched
  and still need calls: daily-loss-breaker posture, the permanently-red
  `test_config_yaml_matches_the_module_defaults`,
  `strike_inside_proxy_noise_floor`, and the 220w-vs-500w noise floor.
