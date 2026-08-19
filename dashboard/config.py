"""Dashboard configuration: paths, colors, refresh cadence, thresholds.

Deliberately stdlib-only. `config` is imported by `db_reader` (pure), by
`charts` (plotly) and by `app` (streamlit); keeping it dependency-free means
the test suite can import it without pulling in a web framework.

Nothing here writes anything. The dashboard is a reader.
"""
from __future__ import annotations

import os

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

#: Repo root. `dashboard/config.py` -> `dashboard/` -> root.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The engine's SQLite database. Env override exists so a test or a copy of
#: the DB can be pointed at without editing code; there is no override for the
#: HALT file (see `engine/halt.py` on why a kill switch gets no escape hatch).
DB_PATH = os.environ.get('TRADING_BOT_DB', os.path.join(PROJECT_ROOT, 'db', 'trading.db'))

#: Research artifacts for the Graveyard tab. These are files on disk, not DB.
GRAVEYARD_SUMMARY_PATH = os.path.join(PROJECT_ROOT, 'research', 'graveyard', 'summary.json')
JUDGE_PACK_PATH = os.path.join(PROJECT_ROOT, 'research', 'judge_evidence_pack.json')
HARNESS_VALIDATION_PATH = os.path.join(PROJECT_ROOT, 'research', 'graveyard', 'harness_validation.json')

#: Fallback kill-switch path, used ONLY if `engine.halt` cannot be imported.
#: `engine/halt.py` is the single definition; duplicating the path is the exact
#: bug that module exists to prevent, so this is a last resort, not a peer.
HALT_FILE_FALLBACK = os.path.join(PROJECT_ROOT, 'HALT')

# --------------------------------------------------------------------------
# Refresh cadence (seconds)
# --------------------------------------------------------------------------

#: Overview and Live Trades: the tabs that answer "what is happening now".
REFRESH_LIVE = 5
#: Strategies, Graveyard, Agent Activity: slow-moving, expensive to compute.
REFRESH_SLOW = 60

#: Cache TTLs. Matched to the refresh cadence so a refresh actually re-reads
#: instead of serving the same cached frame back to itself.
TTL_LIVE = 4
TTL_SLOW = 55
#: Research JSONs are hundreds of KB and change only when a sweep is re-run.
TTL_RESEARCH = 300

#: No equity snapshot newer than this means the engine is not writing.
#: The engine snapshots every 15 min (schema.sql), so 35 min is ~2 missed
#: snapshots - late enough to not cry wolf on a slow tick, early enough to
#: notice a dead process before the next session.
STALE_EQUITY_MINUTES = 35

# --------------------------------------------------------------------------
# Row limits. A dashboard that tries to render 500k rows is a dashboard that
# hangs; every table is capped and says so when it truncates.
# --------------------------------------------------------------------------

MAX_TRADE_ROWS = 500
MAX_ORDER_ROWS = 200
MAX_AUDIT_ROWS = 300
MAX_RISK_ROWS = 100
MAX_EQUITY_POINTS = 5000

# --------------------------------------------------------------------------
# Color. Validated with the dataviz skill's palette validator against this
# page surface (#0a0a0a), dark mode:
#
#   categorical 8 slots -> ALL CHECKS PASS (worst adjacent CVD dE 8.4)
#
# The status pair below (profit green / loss red) does NOT clear CVD
# separation - green vs red measures dE 4.1 under deuteranopia, which is the
# oldest trap in finance UI. Mitigation is structural and non-optional: every
# number colored by sign also carries an explicit +/- sign, and every row
# colored by outcome also carries a WIN/LOSS/OPEN text label. Color is the
# second channel here, never the only one.
# --------------------------------------------------------------------------

# Surfaces and ink
SURFACE_PAGE = '#0a0a0a'
SURFACE_CHART = '#111110'
SURFACE_CARD = '#141413'
INK_PRIMARY = '#ffffff'
INK_SECONDARY = '#c3c2b7'
INK_MUTED = '#898781'
GRIDLINE = '#2c2c2a'
AXIS_LINE = '#383835'
BORDER = 'rgba(255,255,255,0.10)'

# Status (fixed roles, never reused as a series color)
STATUS_GOOD = '#0ca30c'
STATUS_WARNING = '#fab219'
STATUS_SERIOUS = '#ec835a'
STATUS_CRITICAL = '#d03b3b'

# Semantic aliases used throughout the app
PROFIT = STATUS_GOOD
LOSS = STATUS_CRITICAL
OPEN = STATUS_WARNING
NEUTRAL = INK_MUTED

#: Fixed categorical order. Assigned by slot, never cycled. Slots 1-3 are the
#: only ones safe for all-pairs comparison (scatter, small multiples); past
#: three, fold into "Other" or facet.
CATEGORICAL = [
    '#3987e5',  # blue
    '#d95926',  # orange
    '#199e70',  # aqua
    '#c98500',  # yellow
    '#d55181',  # magenta
    '#008300',  # green
    '#9085e9',  # violet
    '#e66767',  # red
]

#: Single-hue ramp for magnitude (light -> dark). On the dark surface the
#: readable end is the light end, so sequential bars step DOWN this list.
SEQUENTIAL_BLUE = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']

# --------------------------------------------------------------------------
# Theme toggle. Two chrome palettes (surface/ink/gridline), same accent
# colors (PROFIT/LOSS/CATEGORICAL/etc.) in both - only the page chrome flips.
# `THEME_DARK` mirrors the module constants above rather than replacing them,
# so anything importing `config.SURFACE_PAGE` etc. directly is unaffected by
# the toggle; `components.inject_css` and `charts._base_layout` take an
# explicit `theme` argument instead of reading global state.
# --------------------------------------------------------------------------

THEME_DARK = dict(
    SURFACE_PAGE=SURFACE_PAGE, SURFACE_CARD=SURFACE_CARD, SURFACE_CHART=SURFACE_CHART,
    INK_PRIMARY=INK_PRIMARY, INK_SECONDARY=INK_SECONDARY, INK_MUTED=INK_MUTED,
    GRIDLINE=GRIDLINE, AXIS_LINE=AXIS_LINE, BORDER=BORDER, PLOTLY_TEMPLATE='plotly_dark',
)
THEME_LIGHT = dict(
    SURFACE_PAGE='#f6f6f4', SURFACE_CARD='#ffffff', SURFACE_CHART='#ffffff',
    INK_PRIMARY='#15150f', INK_SECONDARY='#3d3c36', INK_MUTED='#6f6e66',
    GRIDLINE='#e2e1db', AXIS_LINE='#c8c7c0', BORDER='rgba(0,0,0,0.12)', PLOTLY_TEMPLATE='plotly_white',
)
THEMES = {'Dark': THEME_DARK, 'Light': THEME_LIGHT}

#: Monospace stack. Numbers in a trading terminal align or they lie.
FONT_MONO = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace'

# --------------------------------------------------------------------------
# Domain thresholds
# --------------------------------------------------------------------------

#: A strategy whose signals almost never fire. CLAUDE.md records 9 of 55 at or
#: above this line; the Graveyard tab flags them rather than burying them.
NON_FIRING_ZERO_TRADE_FRACTION = 0.99

#: Lifecycle states from db/schema.sql, in promotion order.
STRATEGY_STATUSES = ['candidate', 'shadow', 'live', 'retired']

STATUS_COLORS = {
    'live': STATUS_GOOD,
    'shadow': STATUS_WARNING,
    'candidate': INK_MUTED,
    'retired': STATUS_CRITICAL,
}

#: Quote currencies that make a `BASE/QUOTE` pair a crypto pair. Polymarket
#: positions carry a market slug in the same column (strategies/polymarket/
#: base.py sets `pair=decision.market_slug`), so shape alone is ambiguous and
#: the strategy prefix is checked first.
CRYPTO_QUOTES = {'USDT', 'USD', 'USDC', 'BUSD', 'DAI', 'EUR', 'BTC', 'ETH'}

#: Strategy id prefixes that mean Polymarket (strategies/polymarket/*.py).
POLYMARKET_PREFIXES = ('PM_', 'pm_', 'polymarket')

ASSET_CLASSES = ['CRYPTO', 'POLYMARKET', 'UNKNOWN']
