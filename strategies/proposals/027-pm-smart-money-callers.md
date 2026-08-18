---
name: "pm_smart_money_callers"
thesis: "A public caller with a verifiable two-year track record names high-conviction directional plays in advance (e.g. NBIS long before earnings, then MRVL puts 9/25). Followers who mirror the declared direction at the time of the post capture part of that move. On Polymarket, the same direction is expressible as a binary contract on the same underlying (stock event markets), so the strategy is: watch named public callers, and when a caller declares a direction on a tradeable Polymarket market, mirror it at small fixed size. This is smart_money_copy's pattern with a different and higher-signal source: a named human with a published, checkable record, not an anonymous wallet."
expected_edge_bps: null
kill_condition: "After the caller feed is wired, if PM_smart_money_callers enters on fewer than 1% of evaluations over 500 or more shadow cycles as measured by agents/forge_shadow_eval.py against db/trading.db, and scores no better than 0 net cents per share over 100 or more resolved positions in backtest/polymarket_harness.py, it is retired rather than repaired a second time. A caller whose declared plays lose 10 or more consecutive resolved positions at small fixed size is dropped from the watchlist."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "Declared direction is mirrored only on Polymarket markets that resolve on the same underlying and date (e.g. 'MRVL above X by date Y' for an MRVL put call). Fixed small premium per play (Kelly default bankroll, capped at the strategy's position limit). Entry at the prevailing ask when the market is within the declared strike and expiry window. Positions held to resolution (no simulated sell path), matching the shadow loop's existing Polymarket behavior. A caller must have at least 3 declared plays with verifiable outcomes before any capital is allocated; the first 3 plays run at the minimum size."
data_requirements: "NEW: a caller feed. Start with one caller (zin1422, r/wallstreetbets, verified 9,155% two-year public track record with pre-declared plays). Source: Reddit posts/comment history via public JSON. BLOCKER: mapping a declared stock direction to a tradeable Polymarket stock-event market, and confirming that market's liquidity and resolution. Until that mapping exists the strategy is NOT_TESTED (convention 11) and must not be reported as having looked and declined."
related_graveyard_findings: "None. No graveyard rows cover caller-following. Nearest live relative: smart_money_copy (follows anonymous wallets, currently blocked on wallet_record_unmeasured because live /trades rows carry no settlement flags). This proposal deliberately starts from a source that resolves publicly and verifiably, so the settlement problem is smaller by construction. D-268: every Polymarket strategy is NOT_TESTED until backtest/polymarket_harness.py scores it."
kind: new
status: PROPOSED
source: "Raven analysis of r/wallstreetbets post 1vmjgx1 (u/zin1422, 2026-08-12): 30K to 2.7M in two years, 9,155%, NBIS earnings FDs at 150% IV 5x, then MRVL puts 9/25 declared in advance. Thread confirms followers profited from the NBIS call."
forge_warnings: "Survivorship bias is extreme in this source class: WSB threads that work get upvoted, threads that blow up get deleted. The strategy must not copy specific plays (MRVL puts) without a live market mapping; it follows the caller's declared direction only where a tradeable Polymarket market exists. Sizing stays small: the caller himself reported being down almost 2M at one point, so the account path is violently convex and the strategy inherits that shape only at fixed small premium."
---

## What this is

A new Polymarket strategy, adjacent to smart_money_copy but with a named, verifiable signal source.

The source post (r/wallstreetbets, 2026-08-12, u/zin1422): a portfolio that went from 30K to 2.7M in two years (9,155%), with 2M of that in the final two months. The post body declares the next play in advance (MRVL puts, 200 strike, 9/25 expiry) after selling the prior winner (NBIS, bought as short-dated options before earnings at high IV, 5x gain). Followers in the thread confirm they profited from the prior declared NBIS play.

## Why this is testable where smart_money_copy is blocked

smart_money_copy is blocked on `wallet_record_unmeasured`: live /trades rows carry no `won`, no `realized_pnl`, no redemption flag, so settlement cannot be scored. A named public caller resolves publicly: the underlying stock price on the declared expiry is a public fact, and Polymarket resolves the contract on it. The settlement problem is smaller by construction.

## The honest limits

- Survivorship bias: this is one posted outcome from a heavily censored class. One caller is a sample of one, which is why the kill condition demands 100+ resolved positions before any edge claim, and why expected_edge_bps is null until measured.
- Copying specific plays (MRVL puts) is not the strategy. The strategy is following the caller's declared direction where a tradeable market exists. If Polymarket has no MRVL contract on that expiry, the play is skipped, not improvised.
- The caller's own path includes a near-2M drawdown. At fixed small premium this is survivable; at any size beyond that it is not. The size cap is load-bearing.

## What is needed to implement

1. Caller feed: poll the caller's Reddit posts/comments for declared plays (direction, strike, expiry).
2. Market mapping: find Polymarket stock-event markets on the same underlying and expiry, confirm liquidity and resolution rule.
3. Shadow wiring: register the strategy, add to the loop, size at minimum until 100+ resolved positions.

## Queue note

This proposal is queued behind the current build (full Polymarket market-type wiring). It is independent of it: it can be implemented as its own strategy file and registered alongside the 19 existing ones without touching the market-type expansion work.
