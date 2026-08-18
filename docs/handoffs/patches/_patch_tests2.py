import io
import sys

PATH = 'tests/test_fair_value_arb.py'
EDITS = [
    ("""    def test_imbalance_is_capped_below_the_speed_cap(self):
        # Book depth is the closest thing here to reading the market's own
        # price. It is capped tightest on purpose so "our" fair value cannot
        # quietly become "their" price restated.
        assert fva  # keep the import meaningful in this class
        strongest_imbalance = imbalance_signal(1.0)
        strongest_speed = speed_signal(1000.0, 0.001)
        assert strongest_imbalance.strength < strongest_speed.strength""",
     """    def test_imbalance_is_capped_below_the_speed_cap(self):
        # Book depth is the closest thing here to reading the market's own
        # price, so it is capped tightest on purpose: "our" fair value must not
        # be able to become "their" price restated. Even at a maximally
        # one-sided book it moves fair value by ~6c, which is inside the range
        # the entry threshold refuses on its own.
        strongest_imbalance = imbalance_signal(1.0)
        strongest_speed = speed_signal(1000.0, 0.001)

        assert strongest_imbalance.strength < strongest_speed.strength
        assert abs(revise_probability(0.5, strongest_imbalance.multiplier)
                   - 0.5) < 0.07"""),
    ("""    def test_speed_is_signed_and_bounded_by_its_cap(self):
        fast_up = speed_signal(50.0, 1.0)
        fast_down = speed_signal(-50.0, 1.0)

        assert fast_up.multiplier > 1.0 > fast_down.multiplier
        assert fast_up.strength <= fva.__dict__.get('SPEED_LOG_CAP', 0.35) or True
        assert fast_up.strength <= 0.35 + 1e-9""",
     """    def test_speed_is_signed_and_bounded_by_its_cap(self):
        from engine.polymarket.fair_value import SPEED_LOG_CAP

        fast_up = speed_signal(50.0, 1.0)
        fast_down = speed_signal(-50.0, 1.0)
        absurd = speed_signal(40_000.0, 0.001)

        assert fast_up.multiplier > 1.0 > fast_down.multiplier
        assert fast_up.strength == pytest.approx(fast_down.strength)
        # A 40,000x speed spike must not become a 40,000x multiplier.
        assert absurd.strength <= SPEED_LOG_CAP + 1e-9"""),
]


def main() -> int:
    with io.open(PATH, encoding='utf-8') as f:
        text = f.read()
    for i, (old, new) in enumerate(EDITS, 1):
        n = text.count(old)
        if n != 1:
            sys.stderr.write('PATCH {} matched {} times\n'.format(i, n))
            return 1
        text = text.replace(old, new, 1)
    with io.open(PATH, 'w', encoding='utf-8') as f:
        f.write(text)
    print('patched', PATH)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
