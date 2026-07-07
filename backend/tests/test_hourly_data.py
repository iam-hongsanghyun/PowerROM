"""The real weather-year hourly profiles (backend/data/hourly/<CC>/<year>.csv.gz) must load,
carry a physically sensible solar shape, and conserve each country's annual capacity factor.

Built by backend/data/build_hourly_profiles.py (real hourly solar from PVGIS, scaled to the
Ember annual CF). Guards the ``data`` dispatch mode the app now defaults to.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.core.hourly_profiles import HOURLY_DATA_DIR, HOURS_PER_YEAR, load_hourly_profiles
from backend.core.lcoe_engine import PROFILE_DIR, load_country_profile

_CODES_WITH_DATA = sorted(p.name for p in HOURLY_DATA_DIR.glob("*") if p.is_dir()) if HOURLY_DATA_DIR.exists() else []
_ALL_PROFILE_CODES = sorted(p.stem for p in PROFILE_DIR.glob("*.json"))


def test_every_country_ships_hourly_data() -> None:
    assert HOURLY_DATA_DIR.exists(), "no hourly data directory shipped"
    missing = [c for c in _ALL_PROFILE_CODES if not (HOURLY_DATA_DIR / c).is_dir()]
    assert not missing, f"countries without hourly weather data: {missing}"


@pytest.mark.skipif(not _CODES_WITH_DATA, reason="no hourly data present")
@pytest.mark.parametrize("code", _CODES_WITH_DATA)
def test_data_mode_loads_and_conserves_annual_cf(code: str) -> None:
    profile = load_country_profile(code)
    years = load_hourly_profiles(code, profile, mode="data")
    assert years, f"{code}: data mode returned no profiles"
    for yp in years:
        assert yp.source.startswith("data:"), f"{code}: fell back to {yp.source}"
        assert len(yp.solar_cf) == HOURS_PER_YEAR
        # Real shape, but its annual mean must still equal the Ember-derived solar CF (± a small
        # tolerance from normalisation/clipping), so switching modes changes shape, not energy.
        assert yp.solar_cf.mean() == pytest.approx(profile["generators"]["solar"]["cf_base"], abs=0.01)


@pytest.mark.skipif("KR" not in _CODES_WITH_DATA, reason="KR data not present")
def test_solar_shape_peaks_at_local_midday_and_is_dark_at_night() -> None:
    profile = load_country_profile("KR")
    yp = load_hourly_profiles("KR", profile, mode="data")[0]
    by_hour = yp.solar_cf.reshape(-1, 24).mean(axis=0)
    assert 10 <= int(np.argmax(by_hour)) <= 13   # peaks around local noon
    assert by_hour[:4].mean() < 0.01             # essentially zero overnight


def test_data_mode_falls_back_to_parametric_without_files() -> None:
    # A country with a profile but no hourly directory must fall back, not crash.
    profile = load_country_profile("KR")
    years = load_hourly_profiles("ZZ", profile, mode="data")  # no hourly/ZZ dir
    assert years and all(y.source == "parametric_synthetic" for y in years)
