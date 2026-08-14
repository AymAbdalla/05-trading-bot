"""Pins for the P4 precheck math (backtest/vr_fingerprint.py).

The precheck's verdict killed a proposal, so the two functions it rests on
get regression pins: a wrong Spearman or a wrong VR here would have
buried/spared P4 for the wrong reason.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.vr_fingerprint import spearman, vr, log_returns


def test_spearman_perfect_and_inverse():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert abs(spearman(list(zip(xs, xs))) - 1.0) < 1e-9
    assert abs(spearman(list(zip(xs, xs[::-1]))) + 1.0) < 1e-9


def test_spearman_is_rank_based_not_linear():
    # monotone but wildly non-linear: rank correlation must still be 1
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [math.exp(x) for x in xs]
    assert abs(spearman(list(zip(xs, ys))) - 1.0) < 1e-9


def test_spearman_handles_ties():
    rho = spearman([(1.0, 1.0), (2.0, 2.0), (2.0, 2.0), (3.0, 3.0)])
    assert rho is not None and rho > 0.99


def test_vr_below_one_for_mean_reverting_series():
    # hard alternation: every 1-bar move is immediately undone ->
    # multi-period variance collapses -> VR(5,1) far below 1
    closes, px = [], 100.0
    for i in range(600):
        px *= 1.01 if i % 2 == 0 else 1 / 1.01
        closes.append(px)
    assert vr(closes, 5, 1) < 0.5


def test_vr_above_one_for_trending_series():
    # persistent drift blocks of one sign -> multi-period variance grows
    # faster than iid -> VR(5,1) above 1
    closes, px, sign = [], 100.0, 1
    for i in range(600):
        if i % 25 == 0:
            sign = -sign
        px *= (1 + sign * 0.01)
        closes.append(px)
    assert vr(closes, 5, 1) > 1.5


def test_log_returns_non_overlapping():
    closes = [100.0, 110.0, 121.0, 133.1]
    rets = log_returns(closes, 1)
    assert len(rets) == 3
    assert all(abs(r - math.log(1.1)) < 1e-12 for r in rets)
    assert len(log_returns(closes, 3)) == 1
