"""Subprocess conformance runner for candidate strategies (T8).

Executed as: python3 sandbox/_runner.py <strategy_file.py>
Loads the module, finds Strategy subclasses, instantiates each with no args,
checks the interface, and runs scan() against synthetic candles. Prints one
JSON object to stdout. Run THIS in a subprocess with a timeout: a hanging or
crashing candidate kills this process, never the engine.

NOTE: this runner executes the candidate's code. It must only ever be called
AFTER the AST allowlist check passed (validator.py enforces that order).
"""
import importlib.util
import json
import sys


def synthetic_candles(n: int = 300):
    # Deterministic wiggly series: enough bars for any builtin lookback.
    closes, highs, lows, opens, vols, ts = [], [], [], [], [], []
    px = 100.0
    for i in range(n):
        px = px * (1.0 + (0.003 if i % 5 else -0.006) + (i % 7 - 3) * 0.0005)
        opens.append(px * 0.999)
        closes.append(px)
        highs.append(px * 1.004)
        lows.append(px * 0.996)
        vols.append(100.0 + (i % 11) * 20.0)
        ts.append(1700000000000 + i * 900000)
    return {'opens': opens, 'highs': highs, 'lows': lows, 'closes': closes,
            'volumes': vols, 'timestamps': ts}


def main(path: str) -> dict:
    sys.path.insert(0, '.')
    from strategies.base import Strategy, Signal

    spec = importlib.util.spec_from_file_location('candidate_strategy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    found = []
    for obj in vars(module).values():
        if (isinstance(obj, type) and issubclass(obj, Strategy)
                and obj is not Strategy):
            found.append(obj)
    if not found:
        return {'ok': False, 'error': 'no Strategy subclass found'}

    results = []
    for cls in found:
        try:
            inst = cls()
        except TypeError as e:
            return {'ok': False, 'error': f'{cls.__name__} not instantiable with no args: {e}'}
        name = getattr(inst, 'name', None)
        is_entry = getattr(inst, 'is_entry', None)
        if not name or not isinstance(name, str):
            return {'ok': False, 'error': f'{cls.__name__}.name missing/invalid'}
        if not isinstance(is_entry, bool):
            return {'ok': False, 'error': f'{cls.__name__}.is_entry missing/invalid'}

        window = synthetic_candles()
        fired = 0
        for end in range(120, 300):
            sub = {k: v[:end] for k, v in window.items()}
            sig = inst.scan(sub)
            if sig is not None:
                if not isinstance(sig, Signal):
                    return {'ok': False, 'error': f'{name}: scan() returned non-Signal'}
                if sig.direction == 'bullish' and (sig.entry is None or sig.stop is None):
                    return {'ok': False, 'error': f'{name}: bullish signal without entry/stop'}
                if sig.direction == 'bullish' and sig.stop is not None and sig.entry is not None \
                        and sig.stop >= sig.entry:
                    return {'ok': False, 'error': f'{name}: stop >= entry on a long'}
                fired += 1
        results.append({'class': cls.__name__, 'name': name,
                        'is_entry': is_entry, 'signals_on_synthetic': fired})

    return {'ok': True, 'strategies': results}


if __name__ == '__main__':
    try:
        out = main(sys.argv[1])
    except Exception as e:
        out = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
    print(json.dumps(out))
    sys.exit(0 if out.get('ok') else 1)
