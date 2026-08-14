"""Contract instruments cannot be sized in dollars. These tests pin the three
things that differ, because ignoring them produced 79,642 fictional futures
rows in the graveyard."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.instruments import (spec_for, option_spec, affordability_report,
                                  FUTURES_SPECS)


class TestContractsAreIndivisible:
    def test_futures_below_one_contract_is_zero_not_fractional(self):
        """The graveyard bug: a $100 clip 'traded' futures. It cannot."""
        spec = spec_for('ES_F', 'FUTURES')
        assert spec.size_for(100, 6800) == 0.0
        assert spec.size_for(1800, 6800) == 1.0

    def test_options_below_one_contract_is_zero(self):
        spec = option_spec()
        assert spec.size_for(100, 5.00) == 0.0    # $500 contract, $100 budget
        assert spec.size_for(500, 5.00) == 1.0

    def test_spot_stays_fractional(self):
        spec = spec_for('AAPL', 'EQUITY')
        assert spec.size_for(100, 200) == pytest.approx(0.5)


class TestCapitalAtRiskIsNotExposure:
    def test_futures_margin_is_far_below_exposure(self):
        """A $34k MES position commits ~$1,800. Reporting returns against
        exposure understates both return and risk by roughly 20x."""
        spec = FUTURES_SPECS['MES']
        exposure = spec.exposure(6800, 1)
        risk = spec.capital_at_risk(6800, 1)
        assert exposure > 30_000
        assert risk == 1_800
        assert exposure / risk > 15

    def test_option_premium_is_the_max_loss(self):
        spec = option_spec()
        assert spec.capital_at_risk(5.00, 1) == 500.0

    def test_spot_risk_equals_exposure(self):
        spec = spec_for('BTC/USDT', 'CRYPTO')
        assert spec.capital_at_risk(50_000, 0.002) == spec.exposure(50_000, 0.002)


class TestMicroRouting:
    def test_standard_contracts_route_to_reachable_micros(self):
        """A small account cannot trade ES; MES trades the same index at 1/10."""
        assert spec_for('ES_F', 'FUTURES').symbol == 'MES'
        assert spec_for('CL_F', 'FUTURES').symbol == 'MCL'

    def test_affordability_report_explains_refusal(self):
        r = affordability_report(100, 6800, 'ES_F', 'FUTURES')
        assert r['tradable'] is False
        assert 'needs' in r['reason']

    def test_two_thousand_dollars_reaches_one_micro_with_real_leverage(self):
        r = affordability_report(2000, 6800, 'ES_F', 'FUTURES')
        assert r['tradable'] is True
        assert r['units'] == 1
        assert r['leverage'] > 10      # this is the risk nobody sees in bps
