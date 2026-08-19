# Proposal 037 - opportunity report (tape-only gate)

**Agent:** `cody-036-037`, 2026-08-19 ~03:55 EDT. Directive:
`docs/handoffs/from-raven/2026-08-19-proposal-037.md` (Task 1), chained from
`docs/handoffs/from-raven/2026-08-19-036complete-and-037.md` (Task 3).
**HEAD at run:** `55b3259`. Tree clean, D-333 guard cleared, no sibling
`claude -p` alive.

## Verdict

**GATE NOT PASSED. Recorded NOT_TESTED (convention 11). The strategy was NOT
built.** 0 qualifying pairs, against a floor of 100.

This is NOT the finding "complement mispricing is rare." It is the finding
**the quantity the gate asks about is not observable in this tape at all** -
see "Why NOT_TESTED and not FAILED" below. Do not retire 037 on this report,
and do not build it either.

## What was measured

The directive's gate, using 036's complement key exactly as
`agents/forge_complement_check.py` defines it (bidirectional exact-key join,
same timestamp):

```sql
from market_tape a join market_tape b
  on a.complement_id = b.market_id
 and b.complement_id = a.market_id
 and a.ts = b.ts
where a.best_ask is not null and b.best_ask is not null
  and a.market_id < b.market_id
```

Qualifying = `a.best_ask + b.best_ask <= 0.996` (the 4-tick / ~40 bps gate).

Counts are **deduplicated** on `(market_id_a, market_id_b, ts)`. The raw join
returns 551 where the distinct count is 359: `market_tape` holds some markets
twice at the same `ts` (876 rows vs 686 distinct `(market_id, ts)`), and the
join multiplies them. A raw `count(*)` here overstates by ~1.5x.

## The numbers

| quantity | value |
|---|---|
| complement-keyed tape window | 2026-08-19 07:28:36 -> 07:55:09 UTC (**26.6 minutes**) |
| distinct pair-observations | **359** |
| distinct complement pairs | 17 |
| distinct poll timestamps | 25 |
| **qualifying (`sum <= 0.996`)** | **0** |
| min / median / max ask-sum | **1.001** / 1.001 / 1.101 |

Ask-sum distribution (deduped), head:

```
1.0010   247
1.0020    30
1.0040    25
1.0100    25
1.0030     3
1.0070     3
```

Nothing at or below 1.000. The minimum is 1.001, which is one tick above par.

## The structural finding (the actual result)

In **359 of 359** pair-observations the complement leg is the *exact*
arithmetic reflection of the first, to 1e-9, on every field:

```
b.best_ask == 1 - a.best_bid
b.best_bid == 1 - a.best_ask
b.mid      == 1 - a.mid
```

Which means, identically, in 359 of 359:

```
yes_ask + no_ask == 1 + (a.best_ask - a.best_bid)  ==  1 + spread
```

**Buying both legs at ask costs 1.00 plus the spread, always.** The gate
`yes_ask + no_ask <= 0.996` is not merely unmet - it is **unsatisfiable by
construction** while the spread is positive. The observed spread floor is
0.001 (247 of 359 observations), so the ask-sum floor is 1.001.

This is not our arithmetic. `engine/polymarket/context.py:348` fetches each
outcome's book independently (`fetch_orderbook(client, outcome.token_id)`,
one CLOB `/book` call per token id), and `strategies/polymarket/dip_arb.py`
`observe()` writes each token's own `book.best_bid` / `book.best_ask`
verbatim. Neither layer derives one leg from the other. **The reflection is
venue-side**: Polymarket's CLOB expresses a YES bid at `p` as a NO ask at
`1-p`, so the two token books are one book seen from two sides.

If that holds generally, proposal 037's premise is refuted structurally rather
than statistically: there is no complement arbitrage to harvest at
top-of-book, ever, and the pair "profit locked at entry" is really
"pay the spread for a certainty."

## Why NOT_TESTED and not FAILED

Two independent reasons, either sufficient:

1. **Sampling.** The gate asks for a count over **14 days**. The
   complement-keyed tape is **26.6 minutes** old - `complement_id` only began
   recording at the 03:28 EDT restart that picked up 036. Every tape row
   before that is NULL-keyed and cannot be paired at all. A 26-minute window
   cannot support the claim "fewer than 100 in 14 days."
2. **The measurement is degenerate.** A count of 0 here is not an estimate of
   how often complements are mispriced; it is a restatement of the venue's
   book identity. Reporting it as "ran and found nothing" would file a venue
   construction fact under a strategy's frequency estimate - exactly the
   confusion convention 11 exists to prevent.

Per convention 11 and the directive's own Task 1, this is NOT_TESTED, and a
NOT_TESTED gate is not a failure.

## Caveats on the structural claim

State it honestly - it rests on a narrow base:

- 26.6 minutes, 17 pairs, 14-17 condition ids. Strong within the sample
  (359/359, exact), but one restart's worth of tape.
- **Top-of-book only.** Depth beyond level 1 was not examined. The reflection
  identity was checked on `best_bid`/`best_ask`, not on the full ladder.
- The 112 `source='ask'` rows (all `best_ask = 0.001`) pair with nothing: in
  0 of 112 does the complement appear at the same timestamp at all, because
  that leg had no usable book. Those are empty-book markets, not arb
  candidates - but they are also 112 observations the gate silently never
  saw.

**Recommendation:** re-derive over a longer window (>= 24h of keyed tape)
before treating "structurally impossible" as settled. The query is cheap and
costs no capital. If it holds at 24h, 037 should be retired on the mechanism,
which is a much stronger retirement than a frequency count.

## 036 key soundness (incidental)

Checked while running the gate, worth recording: **no `market_id` carries two
different `complement_id`s, and none carries two different `condition_id`s.**
036's key is consistent on live data. The duplicate rows noted above are exact
key duplicates (same market written twice in a cycle), which is benign for
keying but inflates any naive join count.

## What was NOT done

- `strategies/polymarket/complement_no_arb_taker.py` was **not written**. The
  gate is the precondition and it did not pass.
- No tests, no registry entry, no config change, no daemon touched.
- 037's proposal file was **not edited**. Its `forge_refusal:` field is stale
  after D-336 (40 bps now clears the 20 bps floor by 2x), but clearing a
  refusal history belongs to the forge cycle's own bookkeeping, not to this
  session.
