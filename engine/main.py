"""Engine entrypoint: wires collector -> scanner -> executor (paper mode).

Run: python3 -m engine.main
Stop: Ctrl-C, `python3 botctl.py halt` (graceful via HALT file), or launchd.

SPEC 3.2/3.3: three layers in one process. Data layer polls public endpoints
(no API key), signal layer scans closed candles, execution layer is the only
thing that can trade - and in paper mode "trading" is the internal fill
simulator. Live mode is refused here regardless of config until the SPEC 10
go-live criteria AND Aym's explicit approval exist (config mode=live also
requires TRADING_LIVE_ACK, and even then this build only supports paper).
"""
import logging
import os
import signal as os_signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from engine.db import init_schema
from engine.collector import DataCollector
from engine.scanner import Scanner
from engine.adapters.paper import PaperAdapter
from engine.executor import Executor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
logger = logging.getLogger('engine.main')


def load_config() -> dict:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    config = load_config()

    mode = config.get('mode', 'paper')
    if mode != 'paper':
        # Hard refusal: this build trades on paper only. SPEC 2: no version
        # goes live automatically; go-live criteria are prerequisites, not
        # triggers, and require Aym's explicit approval.
        logger.error(f"mode={mode!r} refused: this build supports paper mode only")
        sys.exit(1)

    init_schema()

    collector = DataCollector(config)
    scanner = Scanner(config, collector)
    adapter = PaperAdapter(config)
    executor = Executor(config, collector, adapter, scanner.signal_queue)

    running = {'flag': True}

    def shutdown(signum, frame):
        logger.info('shutdown signal received')
        running['flag'] = False

    os_signal.signal(os_signal.SIGINT, shutdown)
    os_signal.signal(os_signal.SIGTERM, shutdown)

    executor.reconcile_on_boot()  # SPEC 7.3: before trading resumes

    collector.start(poll_interval=15)
    scanner.start(poll_interval=15)
    executor.start()
    logger.info('engine running (paper mode): collector + scanner + executor')

    try:
        while running['flag']:
            time.sleep(1)
    finally:
        # Order matters (re-audit N11): stop the scanner FIRST so no new
        # signals enter the queue, then the executor (which drains what's
        # left), then the collector.
        scanner.stop()
        executor.stop()
        collector.stop()
        logger.info('engine stopped')


if __name__ == '__main__':
    main()
