import json
import pathlib

import numpy as np

from backend.core.hourly_profiles import (
    EnsembleSettings,
    load_hourly_profiles,
    sample_ensemble,
    synthesize_parametric,
)

_MONTH_EDGES = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365]


def _monthly_mean(cf: np.ndarray) -> np.ndarray:
    return np.array([cf[a * 24 : b * 24].mean() for a, b in zip(_MONTH_EDGES[:-1], _MONTH_EDGES[1:])])

_KR = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "data" / "country_profiles" / "KR.json").read_text()
)


def _longest_run(mask: np.ndarray) -> int:
    best = current = 0
    for flag in mask:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


def test_wind_profile_has_a_realistic_multiday_drought() -> None:
    # A smooth synthetic profile never truly calms; a realistic one has multi-day winter
    # Dunkelflaute so the reliability-binding hour is a genuine renewable drought.
    yp = synthesize_parametric("KR", _KR, seed=42, year=2020)

    assert yp.wind_cf.min() < 0.02  # wind actually calms (near-zero), not a ~5% floor
    assert _longest_run(yp.wind_cf < 0.05) >= 24  # sustained deep calm of at least a day
    combined = 0.5 * yp.solar_cf + 0.5 * yp.wind_cf
    assert _longest_run(combined < 0.08) >= 24  # wind + solar are low together (dark & calm)


def test_drought_preserves_annual_energy() -> None:
    # The drought redistributes generation (mean-preserving), it does not delete energy: the
    # realized mean CF still matches the profile's cf_base, so the share->capacity map holds.
    yp = synthesize_parametric("KR", _KR, seed=42, year=2020)
    wind_base = _KR["generators"]["wind_onshore"]["cf_base"]
    solar_base = _KR["generators"]["solar"]["cf_base"]

    np.testing.assert_allclose(yp.wind_cf.mean(), wind_base, rtol=0.02)
    np.testing.assert_allclose(yp.solar_cf.mean(), solar_base, rtol=0.05)


def test_block_bootstrap_preserves_seasonality_and_makes_droughts() -> None:
    # The coherent block-bootstrap sampler must (a) manufacture varied years, (b) conserve annual
    # energy, (c) keep the SOURCE seasonal shape (calendar-aligned — not scrambled), and (d) still
    # contain multi-day droughts so LOLE is not under-stated.
    pool = load_hourly_profiles("KR", _KR, mode="parametric", seed=42)
    assert len(pool) >= 2  # need a pool of source years to resample from
    members = sample_ensemble(
        pool, EnsembleSettings(method="block_bootstrap", n_samples=24, seed=7, block_days=14)
    )

    assert len(members) == 24
    assert not np.array_equal(members[0].wind_cf, members[1].wind_cf)  # variety
    realized = np.mean([m.wind_cf.mean() for m in members])
    np.testing.assert_allclose(realized, _KR["generators"]["wind_onshore"]["cf_base"], rtol=0.03)

    # Seasonality preserved: the ensemble-mean monthly solar shape tracks the source pool's shape.
    pool_monthly = np.mean([_monthly_mean(p.solar_cf) for p in pool], axis=0)
    ens_monthly = np.mean([_monthly_mean(m.solar_cf) for m in members], axis=0)
    assert np.corrcoef(pool_monthly, ens_monthly)[0, 1] > 0.98

    # Multi-day droughts survive the resampling (block length exceeds the drought timescale).
    def longest_run(mask: np.ndarray) -> int:
        best = current = 0
        for flag in mask:
            current = current + 1 if flag else 0
            best = max(best, current)
        return best

    assert max(longest_run(m.wind_cf < 0.05) for m in members) >= 24


def test_parametric_profile_is_deterministic() -> None:
    a = synthesize_parametric("KR", _KR, seed=42, year=2020)
    b = synthesize_parametric("KR", _KR, seed=42, year=2020)
    np.testing.assert_array_equal(a.wind_cf, b.wind_cf)
    np.testing.assert_array_equal(a.solar_cf, b.solar_cf)
