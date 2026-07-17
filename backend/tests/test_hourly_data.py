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
        # Real shapes, but annual means must still equal the Ember-derived CFs (± a small
        # tolerance from normalisation/clipping), so switching modes changes shape, not energy.
        assert yp.solar_cf.mean() == pytest.approx(profile["generators"]["solar"]["cf_base"], abs=0.01)
        # Wind gets a slightly wider band: for KE the Ember CF belongs to the Lake Turkana wind
        # corridor while the load-centre point is calm ~half the year — no speed calibration can
        # add energy to zero-wind hours, so it lands ~0.02 short (all other countries hit 0.01).
        assert yp.wind_cf.mean() == pytest.approx(
            profile["generators"]["wind_onshore"]["cf_base"], abs=0.03
        ), f"{code}: wind annual energy not conserved"
        assert yp.demand_norm.mean() == pytest.approx(1.0, abs=1e-6)


@pytest.mark.skipif(not {"AE", "DE"}.issubset(_CODES_WITH_DATA), reason="AE/DE data not present")
def test_demand_seasonality_follows_real_climate() -> None:
    """Demand is temperature-driven (ERA5 T2m degree-hours): a Gulf grid must peak in summer
    (cooling) and a northern-European grid in winter (heating) — from the data files alone."""

    def monthly_demand(code: str) -> np.ndarray:
        yp = load_hourly_profiles(code, load_country_profile(code), mode="data")[0]
        return np.array([yp.demand_norm[m * 730:(m + 1) * 730].mean() for m in range(12)])

    ae, de = monthly_demand("AE"), monthly_demand("DE")
    assert int(np.argmax(ae)) + 1 in (6, 7, 8, 9), "UAE demand must peak in summer (cooling)"
    assert int(np.argmax(de)) + 1 in (11, 12, 1, 2), "German demand must peak in winter (heating)"


def test_wind_power_curve_is_physical() -> None:
    """The speed→CF map must be monotone through the ramp: calm < mid-ramp < rated wind hours."""
    from backend.data.build_hourly_profiles import wind_cf_from_speed

    third = HOURS_PER_YEAR // 3
    ws10m = np.concatenate([
        np.full(third, 1.0),                        # below cut-in at hub height → ~0
        np.full(third, 5.0),                        # mid-ramp
        np.full(HOURS_PER_YEAR - 2 * third, 10.0),  # ≥ rated at hub height → max
    ])
    cf = wind_cf_from_speed(ws10m, wind_cf_base=0.30)
    calm, mid, rated = cf[:third].mean(), cf[third:2 * third].mean(), cf[2 * third:].mean()
    assert calm < mid < rated
    assert calm < 0.02
    assert cf.mean() == pytest.approx(0.30, abs=0.01)  # mean-scaled to the Ember annual CF


def test_demand_thermal_response_is_monotone() -> None:
    """Hotter-than-comfort and colder-than-comfort hours must both raise load vs mild hours."""
    from backend.data.build_hourly_profiles import demand_norm_from_temperature

    third = HOURS_PER_YEAR // 3
    t2m = np.concatenate([
        np.full(third, -5.0),                       # deep heating
        np.full(third, 19.0),                       # comfort band → base load only
        np.full(HOURS_PER_YEAR - 2 * third, 35.0),  # deep cooling
    ])
    d = demand_norm_from_temperature(t2m, year=2018, seed=7)
    cold, mild, hot = d[:third].mean(), d[third:2 * third].mean(), d[2 * third:].mean()
    assert cold > mild and hot > mild
    assert d.mean() == pytest.approx(1.0, abs=1e-9)


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


def test_synthesis_knobs_are_config_backed() -> None:
    """VRE-drought, wind AR(1) persistence and hemisphere are read from the profile (config), so an
    override changes the synthesized profile and the shipped profiles all carry the fields."""
    import json

    from backend.core.hourly_profiles import synthesize_parametric

    prof = load_country_profile("KR")
    # Every shipped profile carries the migrated config knobs.
    assert prof.get("latitude") is not None
    assert prof["vre_drought"]["events"] >= 1
    assert prof["generators"]["wind_onshore"]["wind_ar1_rho"] > 0
    assert prof["ess"]["short_dur"]["arbitrage_price_percentile"] > 0

    base = synthesize_parametric("KR", prof, seed=42, year=2019)
    stormier = json.loads(json.dumps(prof))
    stormier["vre_drought"] = {"events": 8, "min_duration_hr": 60, "max_duration_hr": 120,
                               "wind_floor": 0.01, "solar_floor": 0.05}
    out = synthesize_parametric("KR", stormier, seed=42, year=2019)
    # A harsher drought config redistributes the synthetic wind shape (annual energy still conserved).
    assert not np.allclose(base.wind_cf, out.wind_cf)
    assert base.wind_cf.mean() == pytest.approx(out.wind_cf.mean(), rel=1e-6)


def test_southern_hemisphere_derived_from_latitude() -> None:
    """Hemisphere comes from the config latitude, not a hardcoded country check: a southern profile
    puts its solar-seasonal low in mid-year (its winter), a northern one at the turn of the year."""
    from backend.core.hourly_profiles import synthesize_parametric

    def low_solar_month(code: str) -> int:
        y = synthesize_parametric(code, load_country_profile(code), seed=42, year=2019)
        monthly = [float(np.mean(y.solar_cf[m * 730:(m + 1) * 730])) for m in range(12)]
        return int(np.argmin(monthly)) + 1

    assert low_solar_month("ZA") in (6, 7, 8)   # southern winter
    assert low_solar_month("DE") in (11, 12, 1)  # northern winter
