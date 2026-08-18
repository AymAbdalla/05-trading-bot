"""D-284: `rising_three_methods` is RETIRED from the active strategy set.

D-278 loosened the pattern's binding `small_reds` clause from 0.7 to 1.0 ATR,
which unblocked that clause 8.6x (70 -> 600 hits), and the pattern still fired
ZERO times. That met D-278's stated kill condition, so D-284 retires it.

Retirement here means exactly one thing, and these tests pin that one thing:
the name is out of the registry the sweep builds strategies from, so no future
sweep can produce a graveyard row for it. The detector function is deliberately
still importable and still correct - it is test data and a cautionary record
(convention 11: its old rows are NOT_TESTED, because it could never fire, not
FAIL, which would claim it was tested and lost).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.patterns_all import (  # noqa: E402
    ALL_PATTERNS, BULLISH_PATTERNS, RETIRED_ENTRY_PATTERNS,
    rising_three_methods)
from strategies.builtin.expanded import (  # noqa: E402
    ENTRY_STRATEGIES_EXPANDED, ALL_EXPANDED)
from strategies.builtin.strategy_lab import STRATEGY_LAB_STRATEGIES  # noqa: E402
from strategies.builtin.strategy_lab_v2 import STRATEGY_LAB_V2_STRATEGIES  # noqa: E402
from strategies.builtin.strategy_lab_v3 import STRATEGY_LAB_V3_STRATEGIES  # noqa: E402
from strategies.builtin.strategy_lab_v4 import STRATEGY_LAB_V4_STRATEGIES  # noqa: E402
from strategies.builtin.strategy_lab_v5 import STRATEGY_LAB_V5_STRATEGIES  # noqa: E402

RETIRED = 'rising_three_methods'


def test_d284_names_rising_three_methods():
    assert RETIRED in RETIRED_ENTRY_PATTERNS


def test_retired_pattern_is_out_of_the_entry_registry():
    """The registry IS the mechanism - expanded.py builds one strategy per key
    in BULLISH_PATTERNS with no filtering of its own."""
    assert RETIRED not in BULLISH_PATTERNS
    assert not (RETIRED_ENTRY_PATTERNS & set(BULLISH_PATTERNS))


def test_retired_pattern_builds_no_strategy():
    assert RETIRED not in {s.name for s in ENTRY_STRATEGIES_EXPANDED}
    assert RETIRED not in {s.name for s in ALL_EXPANDED}


def test_retired_pattern_is_not_in_the_sweep_strategy_list():
    """THE assertion. This list is what `run_incremental_graveyard` iterates,
    so a name absent from it produces no graveyard row from any future sweep.
    Rebuilt here the way the runner builds it rather than imported from the
    runner, so the test does not pass just because the runner imported a stale
    module (convention 22)."""
    sweep_strategies = (ENTRY_STRATEGIES_EXPANDED + STRATEGY_LAB_STRATEGIES
                        + STRATEGY_LAB_V2_STRATEGIES + STRATEGY_LAB_V3_STRATEGIES
                        + STRATEGY_LAB_V4_STRATEGIES + STRATEGY_LAB_V5_STRATEGIES)
    names = {s.name for s in sweep_strategies}
    assert RETIRED not in names
    assert not (RETIRED_ENTRY_PATTERNS & names)


def test_the_runners_own_strategy_list_agrees():
    """Same claim, read off the module the sweep actually imports."""
    from backtest.run_incremental_graveyard import ALL_STRATEGIES
    assert RETIRED not in {s.name for s in ALL_STRATEGIES}


def test_the_detector_survives_retirement():
    """Retired is not deleted. The function stays importable and still returns
    a well-formed result, because it is the cautionary record D-284 kept and
    because `tests/test_d276_d279_min_bars_wiring.py` still exercises D-278
    against it."""
    assert callable(rising_three_methods)
    o = [100.0] * 8
    r = rising_three_methods(o, o, o, o)
    assert isinstance(r, dict) and r['found'] is False
    # Out of the ENTRY registry, and not smuggled back in via another one.
    assert RETIRED not in ALL_PATTERNS
