import math

import numpy as np
import polars as pl
import pytest
from scipy.stats import norm

from mbpe.validation.overfitting import cscv_pbo, daily_returns, expected_max_sharpe, psr


def test_pbo_low_when_one_strategy_has_a_real_edge():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 1, size=(1600, 10))
    r[:, 3] += 0.3  # a persistent edge in one strategy
    res = cscv_pbo(r, n_groups=16)
    assert res["pbo"] < 0.05
    assert (res["winners"] == 3).mean() > 0.95


def test_pbo_averages_half_for_pure_noise():
    # one noise dataset gives a noisy PBO (the splits share data); averaged over datasets it is ~0.5
    pbos = [
        cscv_pbo(np.random.default_rng(s).normal(0, 1, size=(800, 20)), n_groups=10)["pbo"] for s in range(20)
    ]
    assert 0.35 < np.mean(pbos) < 0.65
    assert len(cscv_pbo(np.random.default_rng(0).normal(size=(160, 3)), n_groups=16)["logits"]) == math.comb(
        16, 8
    )


def test_pbo_rejects_degenerate_input():
    with pytest.raises(ValueError):
        cscv_pbo(np.zeros((100, 1)))  # one strategy
    with pytest.raises(ValueError):
        cscv_pbo(np.zeros((100, 4)), n_groups=15)  # odd number of groups


def test_psr_matches_closed_form():
    # normal returns (skew 0, kurtosis 3): PSR = Phi(sr * sqrt(n - 1) / sqrt(1 + sr^2 / 2))
    assert psr(0.1, 101, 0.0, 3.0, 0.0) == pytest.approx(norm.cdf(0.1 * 10 / math.sqrt(1 + 0.01 / 2)))
    assert psr(0.1, 101, 0.0, 3.0, 0.1) == pytest.approx(0.5)


def test_expected_max_sharpe_grows_with_trials():
    assert expected_max_sharpe(10, 0.01) < expected_max_sharpe(100, 0.01) < expected_max_sharpe(2000, 0.01)
    assert expected_max_sharpe(100, 0.0) == 0.0


def test_daily_returns_sum_by_exit_day():
    day = 86_400_000
    t = pl.DataFrame(
        {
            "config": ["a", "a", "b"],
            "exit_time": [day * 10 + 5, day * 10 + 7, day * 12],
            "net_R": [1.0, -0.5, 2.0],
        }
    )
    d = daily_returns(t, day * 10, day * 13, risk=0.01)
    assert d["day"].to_list() == [10, 11, 12]
    assert d["a"].to_list() == pytest.approx([0.005, 0.0, 0.0])
    assert d["b"].to_list() == pytest.approx([0.0, 0.0, 0.02])
