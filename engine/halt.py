"""The kill switch, defined once (SPEC 6.3).

HALT is a file at the repository root. Its presence means one thing, for every
asset class the engine runs:

    no new entries, flatten what can be flattened, and stay that way across
    process restarts until a human runs `botctl.py resume --ack <id>`.

## Why this module exists

The path and the existence check used to be written out twice - once in
`engine/executor.py` and once in `botctl.py` - and a third copy was about to be
added for the Polymarket path. Three copies of a kill switch is three chances
for one of them to point somewhere else, and the failure mode is silent: the
crypto side halts, the Polymarket side keeps trading, and nothing in the logs
says the two disagreed. There is one path here and every caller imports it.

## Deliberately not configurable

No environment override, no config key. A kill switch with an override has an
escape hatch, and an escape hatch on a kill switch is the one bug you cannot
afford. If you need a different path for a test, monkeypatch this module's
`HALT_FILE` and accept that you are patching the safety net.

`is_halted()` is fail-safe in the one direction that matters: an unreadable or
corrupt HALT file still counts as halted. `read_halt()` reports the corruption
separately so `botctl` can print something useful, but nothing anywhere treats
"I could not parse the halt" as "there is no halt" (convention 11: an
unreadable state is not an empty one).
"""
import json
import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

#: Repository root. `engine/halt.py` -> `engine/` -> repo root.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The one and only kill-switch path.
HALT_FILE = os.path.join(PROJECT_ROOT, 'HALT')


def is_halted() -> bool:
    """True when the kill switch is engaged. The single definition.

    Presence of the file is the whole test. Contents are metadata for humans
    (who halted, why, and the ack id needed to resume); a HALT file that is
    empty, truncated or not JSON is still a halt.
    """
    return os.path.exists(HALT_FILE)


def read_halt() -> Optional[dict]:
    """The halt record, or None when not halted.

    Returns `{'_unreadable': <reason>}` for a HALT file that exists but cannot
    be parsed, so a caller can tell "hand-created halt" from "no halt" without
    ever confusing either with a clean one. Never raises.
    """
    if not os.path.exists(HALT_FILE):
        return None
    try:
        with open(HALT_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {'_unreadable': f'{type(exc).__name__}: {exc}'}
    return data if isinstance(data, dict) else {'_unreadable': 'not a JSON object'}


def write_halt(reason: str, halt_id: Optional[str] = None) -> str:
    """Engage the kill switch. Returns the ack id needed to resume.

    Writes unconditionally: callers that must not clobber an existing halt
    (`botctl halt` does, because overwriting would invalidate an ack id a human
    is already holding) check `is_halted()` first. The executor's automatic
    halt deliberately does not - an ops backstop firing during an existing halt
    should still leave a record of itself.
    """
    halt_id = halt_id or uuid.uuid4().hex[:8]
    payload = {'halt_id': halt_id, 'ts': int(time.time() * 1000),
               'reason': reason}
    with open(HALT_FILE, 'w') as f:
        json.dump(payload, f)
    logger.error('HALT written (id=%s): %s', halt_id, reason)
    return halt_id


def clear_halt() -> bool:
    """Remove the HALT file. True if one was there. Ack checking is the
    caller's job (`botctl resume` owns that policy)."""
    if not os.path.exists(HALT_FILE):
        return False
    os.remove(HALT_FILE)
    logger.warning('HALT cleared')
    return True
