import json
import pathlib

import numpy as np

from backend.core.hourly_profiles import synthesize_parametric

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


def test_parametric_profile_is_deterministic() -> None:
    a = synthesize_parametric("KR", _KR, seed=42, year=2020)
    b = synthesize_parametric("KR", _KR, seed=42, year=2020)
    np.testing.assert_array_equal(a.wind_cf, b.wind_cf)
    np.testing.assert_array_equal(a.solar_cf, b.solar_cf)
