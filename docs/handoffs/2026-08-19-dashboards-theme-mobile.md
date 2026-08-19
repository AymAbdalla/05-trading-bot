# Dashboard theme toggle + mobile responsiveness - all three, one shipped

**Session:** `cody-dash-theme`, 2026-08-19, ~08:15-09:05 EDT.
**Brief:** `docs/handoffs/from-raven/2026-08-19-dashboards-theme-mobile.md`.
**Scope:** dashboard-only, as instructed. No engine code touched.

## Verification (re-derived, not quoted)

- Trading bot full suite: **4,081 passed / 1 skipped / 0 failed** (425s),
  `--ignore=tests/test_dashboard_charts.py` as the wake-up file specifies.
  4,072 baseline + 8 new (`tests/test_dashboard_theme.py`) + 1 from a
  concurrent sibling session (see "Found in the tree" below) = 4,081.
- `backtest/validate_harness.py`: **21/21, returncode 0.**
- Career agent full suite (separate repo, separate venv): **265 passed, 0
  failed** (includes 8 new theme tests there too).
- Trading bot dashboard: `curl -o /dev/null -w '%{http_code}' 127.0.0.1:8501`
  -> **200**, restarted to pick up this session's code.
- Career dashboard: same check on **8503** -> **200**, started fresh (was
  not running before this session).
- LP tool web panel: **NOT started.** See "Blocked" below.

## What was built

**Theme system, same pattern in both Streamlit dashboards, no cross-import**
(`05-trading-bot/dashboard/` and `06-career-agent/dashboard/` are separate
repos, deliberately duplicated per the brief).

- `config.py`: added `THEME_DARK` / `THEME_LIGHT` / `THEMES` dicts. `THEME_DARK`
  *mirrors* the existing module constants (`SURFACE_PAGE` etc.) rather than
  replacing them, so anything importing `config.SURFACE_PAGE` directly - other
  modules, existing tests - sees no change. The toggle is threaded as an
  explicit `theme: str` argument everywhere, never a global mutation; that
  matters because `st.fragment` reruns per-tab on independent timers, and a
  module-level "current theme" variable would race across them.
- `components.py`: `inject_css(theme='Dark')` now looks up the palette;
  `theme_toggle()` is a `st.segmented_control('Theme', ['Dark','Light'],
  key='theme')` in the sidebar - the `key` gives it its own session-state
  slot for free, no extra persistence code needed. Also fixed one hardcoded
  `rgba(255,255,255,0.045)` log-line border in the trading bot's CSS (would
  have stayed white-on-white-adjacent in light mode).
- `charts.py`: `_base_layout` and every public chart function take
  `theme: str = 'Dark'` and default identically to prior output - existing
  callers and `test_dashboard_charts.py` (excluded from the run command but
  not touched) are unaffected. `template` is now set to `plotly_dark` /
  `plotly_white` per the brief's instruction; `plot_bgcolor` and the axis/grid
  chrome flip with it, accent colors (PROFIT/LOSS/CATEGORICAL) do not.
- `app.py`: sidebar returns the theme; trading bot's fragments read it via
  `_active_theme()` (session-state indirection, same reason `_active_mode()`
  exists - fragment functions must stay zero-argument, see the docstring on
  `_run_fragment`). Every `st.plotly_chart` call now carries
  `config={'displayModeBar': False, 'responsive': True}` - the brief's mobile
  requirement - via a shared `CHART_CONFIG` constant instead of eight inline
  dicts.

**Mobile CSS**, both dashboards: extended the existing `@media (max-width:
640px)` block (it already existed - smaller cards, smaller `h1` - this session
added tighter top padding and `overflow-x: auto` on `stDataFrame` /
`stElementContainer table` so a wide trade table scrolls inside its own box on
a 390px phone instead of forcing the page to scroll). `st.dataframe` calls
already used `width='stretch'` (the 1.50 successor to `use_container_width`)
everywhere - nothing to change there.

**LP tool** (`research/polymarket_lp_tool/`, Flask/Jinja, not Streamlit): added
a light theme via `:root[data-theme="light"]` CSS variable overrides, a
`◐ 主题` toggle button in the nav, and a pre-paint `<script>` in `<head>` that
reads `localStorage` before the stylesheet loads, so a saved Light preference
never flashes dark first. Added to `login.html` and `error.html` too (they
don't extend `base.html`, so they'd otherwise ignore the saved preference).
Mobile: added a `@media (max-width: 640px)` block - the page already had a
viewport meta tag and an `overflow-x:auto` wrapper around the orders table
from a prior session, so most of task 2 was already done there; this session
tightened padding, table font size, and made `.rule-grid` single-column.

**Tests:** `tests/test_dashboard_theme.py` in both repos (8 tests each, same
file, mirrored per-repo like everything else). Asserts both themes carry every
key the chart/CSS layer reads, that `'Dark'` reproduces the pre-existing
constants exactly (so nothing else regresses), that an unknown theme string
falls back to Dark rather than raising, and that Dark vs Light actually
produce different `plot_bgcolor` / CSS output - not just that both run without
raising.

## Blocked - LP tool could not be started (task 3, partial)

`research/polymarket_lp_tool/` is a **separate git repo, third-party in
origin** (`git remote -v` -> `github.com/lihanyu81/polymarket_lp_tool.git`,
one commit: "feat: release Rust-based Polymarket LP Tool 2.0"). It has:
- **no `.venv`** of its own,
- the trading bot's own `.venv` has **no `flask`** installed
  (`requirements.txt` lists `flask>=3.0.0`, `py-clob-client-v2`,
  `websockets`, `python-dotenv` - none present),
- **no `.env`** - `create_app()`'s `main()` calls `get_ctx()` ->
  `WebPanelContext()`, which needs real Polymarket API credentials, and
  refuses to start without `WEB_PANEL_TOKEN` at all (`main()`:
  `raise SystemExit(1)`).

Starting it means either installing a new dependency set into a shared venv
or standing up a new one, *and* provisioning real trading credentials - both
outside "dashboard-only, don't touch engine code" and outside what I'll do
without asking. The CSS/theme/mobile work is complete and correct by
inspection (Jinja templates, no test harness exists for this module - see
below); it just cannot be exercised end-to-end from this session.

**Also:** because the LP tool repo is third-party-sourced and gitignored from
this repo (`.gitignore:61`), I did **not** commit inside it. `git status`
there shows 4 modified files, uncommitted, local-only. Whether to commit (and
never push - that remote isn't Aym's) is Raven's call.

## Found in the tree, not touched

`git status` at session start showed uncommitted changes to `config.yaml`,
`engine/polymarket/risk_gate.py`, `engine/polymarket/shadow_loop.py`,
`engine/risk/constraints.py`, `engine/risk/events.py`,
`tests/test_polymarket_risk_gate.py`, `tests/test_polymarket_shadow_loop.py` -
none of which this session made. Read-only observation, consistent with
active work on the risk-module wiring (open item 2/3 in CLAUDE.md). Not in
the concurrency ledger (`who` showed only `cody-038-ledger`'s two stale
DECISIONS.md/resolution_ledger.py checkouts), so whoever is editing those
files isn't using `safe_edit`. I did not touch them and committed only my own
files by explicit pathspec.

## Commit

`dashboard/{app,charts,components,config}.py`,
`tests/test_dashboard_theme.py`, this handoff, `CLAUDE.md`. Career agent's
four analogous files plus its own `tests/test_dashboard_theme.py` are in
`06-career-agent`, committed there separately (its own repo, its own
`HANDOFF RULE` - see that project's `docs/handoffs/`).

## Next steps for Raven

1. Decide whether the LP tool repo's local changes should be committed
   (third-party origin, never push).
2. Provision LP tool venv + deps + `.env` if the web panel should actually run
   on 8502, or say it stays Telegram-only.
3. Everything else in "Genuinely open" (CLAUDE.md) is unchanged by this
   session - risk module wiring still blocked on items 2/3, restart still
   scheduled for ~03:45 EDT 2026-08-20.
