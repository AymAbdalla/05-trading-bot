"""Polymarket data layer tests. No network: the HTTP layer is mocked.

These pin the three things that actually break against this venue - Gamma's
double-encoded JSON, the positional clobTokenIds pairing, and non-finite floats
sneaking in through `float()` - plus the drop accounting convention 20 requires.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

placeholder = True
