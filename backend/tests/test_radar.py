"""Analytical tests for the System Radar scoring (backend.core.radar).

Every axis has a closed-form anchor, so each scorer is checked against exact values rather
than captured baselines: the standard scores 75, log-decade steps move ±25, linear axes hit
their endpoints, and the ECDF affordability rank is verified on a hand-built distribution.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.core import radar
from backend.core.radar import (
    AXIS_KEYS,
    CLIMATE_BENCHMARK_G,
    CLIMATE_WORST_G,
    LOLE_STANDARD_HOURS,
    UNSERVED_STANDARD_PPM,
    build_radar,
    compute_axes,
    fold_pillars,
    score_affordability,
    score_climate,
    score_independence,
    score_log_decades,
    score_price_stability,
)


# ── score_log_decades: 75 at the standard, ±25 per factor of 10 ─────────────────

@pytest.mark.parametrize("factor,expected", [
    (1.0, 75.0),     # exactly at the standard
    (0.1, 100.0),    # 10× better → +25
    (10.0, 50.0),    # 10× worse → −25
    (100.0, 25.0),
    (1000.0, 0.0),
    (0.01, 100.0),   # clipped at 100
])
def test_log_decades_anchors(factor: float, expected: float) -> None:
    np.testing.assert_allclose(
        score_log_decades(LOLE_STANDARD_HOURS * factor, LOLE_STANDARD_HOURS),
        expected, rtol=0, atol=1e-9,
    )


def test_log_decades_zero_is_perfect() -> None:
    assert score_log_decades(0.0, LOLE_STANDARD_HOURS) == 100.0
    assert score_log_decades(-1.0, UNSERVED_STANDARD_PPM) == 100.0


def test_log_decades_monotone_decreasing() -> None:
    values = np.logspace(-3, 5, 50)
    scores = [score_log_decades(float(v), LOLE_STANDARD_HOURS) for v in values]
    assert all(a >= b for a, b in zip(scores, scores[1:]))


# ── affordability: interpolated ECDF complement ─────────────────────────────────

def test_affordability_ecdf_anchors() -> None:
    dist = [40.0, 60.0, 80.0, 100.0, 120.0]
    assert score_affordability(30.0, dist) == 100.0   # cheaper than all
    assert score_affordability(200.0, dist) == 0.0    # pricier than all
    np.testing.assert_allclose(score_affordability(80.0, dist), 50.0, atol=1e-9)   # median
    np.testing.assert_allclose(score_affordability(70.0, dist), 62.5, atol=1e-9)   # midpoint interp
    np.testing.assert_allclose(score_affordability(40.0, dist), 100.0, atol=1e-9)


def test_affordability_fallback_without_distribution() -> None:
    lo, hi = radar.AFFORDABILITY_FALLBACK_RANGE
    assert score_affordability(lo, None) == 100.0
    assert score_affordability(hi, None) == 0.0
    np.testing.assert_allclose(score_affordability((lo + hi) / 2, None), 50.0, atol=1e-9)


# ── price stability / independence: linear complements ──────────────────────────

def test_price_stability_linear() -> None:
    assert score_price_stability(0.0, 80.0) == 100.0
    assert score_price_stability(80.0, 80.0) == 0.0
    np.testing.assert_allclose(score_price_stability(20.0, 80.0), 75.0, atol=1e-9)
    assert score_price_stability(10.0, 0.0) == 100.0  # degenerate zero-LCOE guard


def test_independence_linear() -> None:
    assert score_independence(0.0) == 100.0
    assert score_independence(1.0) == 0.0
    np.testing.assert_allclose(score_independence(0.25), 75.0, atol=1e-9)
    assert score_independence(1.5) == 0.0  # clipped


# ── climate: piecewise anchors ──────────────────────────────────────────────────

def test_climate_anchors() -> None:
    assert score_climate(0.0) == 100.0
    np.testing.assert_allclose(score_climate(CLIMATE_BENCHMARK_G), 75.0, atol=1e-9)
    np.testing.assert_allclose(score_climate(CLIMATE_WORST_G), 0.0, atol=1e-9)
    assert score_climate(CLIMATE_WORST_G + 500.0) == 0.0  # clipped beyond coal
    # both segments are linear: check an interior point of each
    np.testing.assert_allclose(score_climate(CLIMATE_BENCHMARK_G / 2), 87.5, atol=1e-9)
    mid = (CLIMATE_BENCHMARK_G + CLIMATE_WORST_G) / 2
    np.testing.assert_allclose(score_climate(mid), 37.5, atol=1e-9)


def test_climate_monotone_and_continuous_at_benchmark() -> None:
    grid = np.linspace(0.0, 1200.0, 200)
    scores = [score_climate(float(g)) for g in grid]
    assert all(a >= b for a, b in zip(scores, scores[1:]))
    eps = 1e-6
    np.testing.assert_allclose(
        score_climate(CLIMATE_BENCHMARK_G - eps), score_climate(CLIMATE_BENCHMARK_G + eps),
        atol=1e-4,
    )


# ── compute_axes / pillars / build_radar assembly ───────────────────────────────

def _metrics(adequacy: dict | None) -> dict:
    return {
        "country": "KR",
        "system_lcoe": 80.0,
        "stack_components": {"fuel": 20.0},
        "emission_intensity": 0.050,   # tCO2/MWh → 50 g/kWh
        "import_dependency": 0.25,
        "annual_demand_twh": 100.0,    # → 1e8 MWh
        "unserved_twh": 0.0002,        # 200 MWh → 2 ppm of demand
        "adequacy": adequacy,
    }


def test_compute_axes_with_ensemble() -> None:
    adequacy = {"lole_hours": 2.4, "unserved_mwh_max": 2000.0, "n_scenarios": 5}  # 20 ppm
    axes = {a["key"]: a for a in compute_axes(_metrics(adequacy), [40.0, 80.0, 120.0])}
    assert tuple(a for a in axes) == AXIS_KEYS
    np.testing.assert_allclose(axes["reliability"]["score"], 75.0, atol=0.05)
    np.testing.assert_allclose(axes["resilience"]["score"], 75.0, atol=0.05)
    np.testing.assert_allclose(axes["price_stability"]["score"], 75.0, atol=0.05)
    np.testing.assert_allclose(axes["independence"]["score"], 75.0, atol=0.05)
    np.testing.assert_allclose(axes["climate"]["score"], 75.0, atol=0.05)
    np.testing.assert_allclose(axes["affordability"]["score"], 50.0, atol=0.05)  # median of 3
    assert axes["reliability"]["unit"] == "h/yr LOLE"
    for axis in axes.values():
        assert 0.0 <= axis["score"] <= 100.0
        assert axis["detail"]


def test_compute_axes_single_run_fallback() -> None:
    axes = {a["key"]: a for a in compute_axes(_metrics(None), None)}
    # 2 ppm unserved is a decade better than the 20 ppm standard → 100 on both adequacy axes
    np.testing.assert_allclose(axes["reliability"]["score"], 100.0, atol=0.05)
    np.testing.assert_allclose(axes["resilience"]["score"], 100.0, atol=0.05)
    assert axes["reliability"]["unit"] == "ppm unserved"


def test_fold_pillars_means() -> None:
    axes = compute_axes(_metrics(None), None)
    pillars = fold_pillars(axes)
    by_key = {a["key"]: a["score"] for a in axes}
    np.testing.assert_allclose(
        pillars["security"],
        round((by_key["reliability"] + by_key["resilience"] + by_key["independence"]) / 3, 1),
        atol=0.05,
    )
    np.testing.assert_allclose(
        pillars["equity"], round((by_key["affordability"] + by_key["price_stability"]) / 2, 1),
        atol=0.05,
    )
    np.testing.assert_allclose(pillars["sustainability"], by_key["climate"], atol=0.05)


def test_build_radar_block_shape() -> None:
    block = build_radar(_metrics(None))
    assert set(block) == {"axes", "pillars", "baseline", "method"}
    assert [a["key"] for a in block["axes"]] == list(AXIS_KEYS)
    assert set(block["pillars"]) == {"security", "equity", "sustainability"}


def test_calculate_response_carries_radar() -> None:
    """End-to-end: the engine attaches a schema-valid radar block to a real calculation."""
    from backend.core.lcoe_engine import calculate_system_lcoe, load_country_profile
    from backend.models.schemas import CalculateResponse

    profile = load_country_profile("KR")
    result = calculate_system_lcoe(country="KR", shares=profile["shares"], carbon_price=0.0)
    response = CalculateResponse(**result)
    assert response.radar is not None
    assert [axis.key for axis in response.radar.axes] == list(AXIS_KEYS)
    for axis in response.radar.axes:
        assert 0.0 <= axis.score <= 100.0
