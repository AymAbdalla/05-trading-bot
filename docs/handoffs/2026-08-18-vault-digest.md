# Vault digest (B9): write-time rolling digest, Forge reads ~10K not 72K

**Cody + Raven, 2026-08-18.** Task: `docs/handoffs/from-raven/2026-08-18-vault-digest.md` (B9 of elevated hybrid setup, Aym approved).

## What shipped

| File | State | What |
|---|---|---|
| `agents/vault_digest.py` | **new** | Rolling digest module: capped at 10K chars, oldest entries archive to `_DIGEST-ARCHIVE.md` (never deleted), concurrency-safe via `engine.concurrency.safe_edit` |
| `agents/vault_writer.py` | modified | Appends a digest entry after every real note write; asks the same Opus turn to emit the structured Verdict/Evidence/Relevance block (no extra model call); dry-run/skip_model does NOT touch the digest |
| `agents/vault_reader.py` | modified | Digest-aware read |
| `agents/forge_reasoner.py` | modified | **Digest-first**: reads `_DIGEST.md` (bounded ~10K), delta notes newer than the digest's newest entry, graph-flagged items. Fallback: digest missing → full tree with `digest_status='missing'`; digest stale (>30d) → full tree with `digest_status='stale'`. Never fails a cycle. Forge output carries `vault_ctx:` line with what it read |
| `scripts/vault_digest_backfill.py` | **new** | One-time seed of `_DIGEST.md` from the 6 pre-digest Trading notes (2 lessons, 2 cards, 2 cycle summaries), `--check`/`--apply` |
| `tests/test_vault_digest.py` + 3 more | **new** | 49 tests: cap enforcement (append past 10K → oldest dropped + archived), dry-run isolation, missing-digest fallback, digest-first read |

## Verified by Raven (not inherited)

- Backfill applied: `~/aym/vault/Trading/_DIGEST.md` seeded, 4,263 bytes, all 6 notes with correct Verdict/Evidence/Relevance (spot-checked against source notes).
- **Full suite: 3,553 passed, 1 skipped** (was 3,504 — +49 new tests), 5m40s.
- Commit `5966773`, working tree clean except live-process outputs (loop CSV, poller wallets — correctly untouched).

## Notes

- Session died before its epilogue (handoff/webhook). Work was complete and tested on disk; Raven committed and is writing this handoff in its place. No work lost.
- The cap is a Convention-17 assumption (10K) with an expiry: when Forge's brief routinely reports the digest at cap with archiving every cycle, grow the cap or shrink the entry format.
- Not done: nothing else. No trading behavior changed. No loop restart.

## For the queue

- Next: 029/030/031 implementation (drafted), then 027, then 028. All queued behind this.
