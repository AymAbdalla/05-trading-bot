"""The per-asset strike-proxy noise floor, and which way the gate points.

Why this file exists
--------------------
The floor was loosened from one global 5.0 bps to a per-asset value (1.0 bps on
btc/eth/sol) so that strike-dependent strategies fire in shadow mode instead of
being gated to silence. The change is small; the way it is MISREAD is not.

The instruction that prompted the change asked to "lower the noise floor to
15bps" in order to let MORE windows through. That is backwards, and the shape
of the mistake is worth pinning in a test rather than a comment: the gate is
`abs(lead_bps) < floor -> skip`, so a BIGGER floor rejects MORE. 15 bps would
have gated every window that 5.0 already gated, plus more, while the stated
goal was the opposite.

`test_a_bigger_floor_never_admits_more` is the guard. If anyone ever "loosens"
this gate by raising the number, it goes red.
"""
import math

import pytest
import yaml

from engine.polymarket.strike import (NOISE_FLOOR_BPS_BY_ASSET,
                                      NOISE_FLOOR_ERROR_MEASURED_AT_BPS,
                                      PROXY_DISAGREEMENT_PCT_BY_BAND,
                                      STRIKE_PROXY_NOISE_FLOOR_BPS,
                                      active_floor_error_pct_for,
                                      disagreement_pct_for_lead,
                                      error_at_floor_pct_for,
                                      is_inside_noise_floor,
                                      noise_floor_bps_for,
                                      set_noise_floor_bps_by_asset)


@pytest.fixture(autouse=True)
def restore_floors():
    """`set_noise_floor_bps_by_asset` mutates module state. Put it back.

    Without this a test that sets a floor leaks into every later test in the
    session, and the failure surfaces somewhere else entirely.
    """
    saved = dict(NOISE_FLOOR_BPS_BY_ASSET)
    yield
    NOISE_FLOOR_BPS_BY_ASSET.clear()
    NOISE_FLOOR_BPS_BY_ASSET.update(saved)


# -- the direction of the gate ---------------------------------------------

def test_a_bigger_floor_never_admits_more():
    """Raising the floor can only reject more windows. Never fewer.

    This is the whole misreading, made mechanical. The gate rejects when
    `abs(lead) < floor`, so the admitted set shrinks monotonically as the floor
    grows. A "loosening" that raises this number is tightening it.
    """
    leads = [0.0, 0.25, 0.9, 1.0, 1.4, 2.6, 3.95, 4.99, 5.0, 12.0, -3.0, -7.5]
    floors = [0.0, 0.5, 1.0, 2.0, 5.0, 15.0, 25.0]
    admitted = [
        sum(1 for lead in leads if not is_inside_noise_floor(lead, floor))
        for floor in floors]
    assert admitted == sorted(admitted, reverse=True), (
        'admitted counts %s are not non-increasing as the floor rises %s'
        % (admitted, floors))
    # And concretely, at the numbers that were proposed as a loosening.
    assert admitted[floors.index(15.0)] < admitted[floors.index(5.0)]
    assert admitted[floors.index(25.0)] <= admitted[floors.index(15.0)]


def test_the_shipped_floor_admits_more_than_the_old_one():
    """The actual change has to move the gate in the intended direction.

    Every window the gate has historically rejected had |lead| < 5.0 by
    construction, so this is the property that matters: at the shipped floor,
    leads that 5.0 rejected now pass.
    """
    for asset in ('btc', 'eth', 'sol'):
        floor = noise_floor_bps_for(asset)
        assert floor < STRIKE_PROXY_NOISE_FLOOR_BPS, asset
        # A lead between the new floor and the old one is exactly the set the
        # change was made to admit.
        midpoint = (floor + STRIKE_PROXY_NOISE_FLOOR_BPS) / 2.0
        assert is_inside_noise_floor(midpoint, STRIKE_PROXY_NOISE_FLOOR_BPS)
        assert not is_inside_noise_floor(midpoint, floor)


def test_the_floor_stays_outside_the_measured_coin_flip_band():
    """No asset may be floored inside the band where the proxy is a coin flip.

    Below 1.0 bps the proxy disagrees with the oracle 42.2% of the time. A
    strategy firing there is sampling a random number generator, and convention
    11 says that is NOT_TESTED rather than a result. Admitting more windows is
    the goal; admitting windows that carry no information is not.
    """
    coin_flip_high = PROXY_DISAGREEMENT_PCT_BY_BAND[0][1]
    for asset, floor in NOISE_FLOOR_BPS_BY_ASSET.items():
        assert floor >= coin_flip_high, (
            '%s floor %s is inside the %s%% coin-flip band below %s bps'
            % (asset, floor, PROXY_DISAGREEMENT_PCT_BY_BAND[0][2],
               coin_flip_high))


# -- per-asset lookup -------------------------------------------------------

def test_every_shadow_asset_has_its_own_floor():
    from engine.polymarket.assets import SHADOW_ASSETS
    for asset in SHADOW_ASSETS:
        assert asset in NOISE_FLOOR_BPS_BY_ASSET, asset


def test_an_unregistered_asset_falls_back_to_the_conservative_default():
    """An unmeasured asset gets the TIGHT floor, not the loose one.

    The failure mode of guessing loose for an asset nobody has measured is a
    strategy firing on a strike whose error is unknown. That is unreadable
    rather than merely noisy, so the fallback is deliberately the strict 5.0.
    """
    assert noise_floor_bps_for('xrp') == STRIKE_PROXY_NOISE_FLOOR_BPS
    assert noise_floor_bps_for(None) == STRIKE_PROXY_NOISE_FLOOR_BPS
    assert noise_floor_bps_for('') == STRIKE_PROXY_NOISE_FLOOR_BPS
    assert noise_floor_bps_for('BTC') == noise_floor_bps_for('btc')


# -- config overrides -------------------------------------------------------

def test_config_overrides_apply_and_are_case_insensitive():
    set_noise_floor_bps_by_asset({'BTC': 2.5, 'sol': 0.0})
    assert noise_floor_bps_for('btc') == 2.5
    assert noise_floor_bps_for('sol') == 0.0
    # A zero floor is legal and means "admit every finite lead".
    assert not is_inside_noise_floor(0.0, noise_floor_bps_for('sol'))


def test_an_empty_override_changes_nothing():
    before = dict(NOISE_FLOOR_BPS_BY_ASSET)
    assert set_noise_floor_bps_by_asset(None) == before
    assert set_noise_floor_bps_by_asset({}) == before


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1.0, 'wide',
                                 None, [1.0]])
def test_a_bad_floor_raises_instead_of_riding_along(bad):
    """A non-finite floor would SILENTLY DISABLE the gate.

    `abs(lead) < nan` is False for every lead, so a NaN floor admits
    everything while reading in the log as a configured number. Convention 19:
    a non-finite must fail loudly, not ride along.
    """
    with pytest.raises(ValueError):
        set_noise_floor_bps_by_asset({'btc': bad})


def test_config_yaml_floors_match_the_module_defaults():
    """Drift between config.yaml and the module fails loudly.

    Same guard the risk gate block has. If someone edits one and not the
    other, the running loop and the reviewed file disagree silently.
    """
    with open('config.yaml') as handle:
        config = yaml.safe_load(handle)
    configured = ((config.get('polymarket') or {})
                  .get('strike_proxy', {})
                  .get('noise_floor_bps_by_asset'))
    assert configured, 'config.yaml has no strike_proxy noise floor block'
    assert {k: float(v) for k, v in configured.items()} == dict(
        NOISE_FLOOR_BPS_BY_ASSET), (
            'config.yaml %s and NOISE_FLOOR_BPS_BY_ASSET %s have drifted'
            % (configured, dict(NOISE_FLOOR_BPS_BY_ASSET)))


# -- the error rate must not be reported at a floor it was not measured at --

def test_the_measured_error_is_not_reported_at_a_floor_it_was_not_measured_at():
    """`active_floor_error_pct_for` is None unless the floor IS 5.0 bps.

    `NOISE_FLOOR_ERROR_BY_ASSET` was measured AT 5.0. Once the active floor
    moves off 5.0 that number stops describing the gate, and publishing it
    under a field that reads as "the error at the floor" is exactly the stale
    number this codebase keeps having to hunt down. None means UNMEASURED.
    """
    set_noise_floor_bps_by_asset({'btc': 1.0})
    assert noise_floor_bps_for('btc') != NOISE_FLOOR_ERROR_MEASURED_AT_BPS
    assert active_floor_error_pct_for('btc') is None
    # The 5.0-bps measurement itself is untouched and still reportable.
    assert error_at_floor_pct_for('btc') == 5.1

    set_noise_floor_bps_by_asset({'btc': NOISE_FLOOR_ERROR_MEASURED_AT_BPS})
    assert active_floor_error_pct_for('btc') == 5.1


def test_an_unmeasured_asset_reads_as_none_at_the_measured_floor():
    set_noise_floor_bps_by_asset({'xrp': NOISE_FLOOR_ERROR_MEASURED_AT_BPS})
    assert active_floor_error_pct_for('xrp') is None


# -- per-evaluation disagreement stamp --------------------------------------

@pytest.mark.parametrize('lead,expected', [
    (0.0, 42.2), (0.4, 42.2), (0.999, 42.2),
    (1.0, 23.5), (1.9, 23.5),
    (2.0, 6.8), (4.99, 6.8),
    (5.0, 6.5), (9.9, 6.5),
    (10.0, 0.0), (250.0, 0.0),
])
def test_disagreement_is_reported_for_the_band_the_lead_sits_in(lead, expected):
    assert disagreement_pct_for_lead(lead) == expected
    # Sign must not matter: a lead is a distance from the strike either way.
    assert disagreement_pct_for_lead(-lead) == expected


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), 'x', object()])
def test_an_unknown_lead_reads_as_none_not_zero(bad):
    """None is UNKNOWN. 0.0 would mean "the proxy is never wrong here".

    Those are opposite claims and must never share a field value
    (convention 20).
    """
    assert disagreement_pct_for_lead(bad) is None


def test_the_bands_are_contiguous_and_cover_every_finite_lead():
    """No gap, no overlap, no lead that falls through to None by accident.

    A gap here would make `disagreement_pct_for_lead` return None for a real
    lead, which reads as "unknown" when the truth is "the table is broken".
    """
    bands = list(PROXY_DISAGREEMENT_PCT_BY_BAND)
    assert bands[0][0] == 0.0
    assert bands[-1][1] == math.inf
    for (_, high, _), (next_low, _, _) in zip(bands, bands[1:]):
        assert high == next_low, 'gap or overlap at %s' % high
