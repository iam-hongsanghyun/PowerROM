"""Every shipped country profile must load and produce a sane system LCOE.

Guards the predefined country data (backend/data/country_profiles/*.json) against a broken
schema or an implausible value slipping in when a new country is added.
"""

import pytest

from backend.core.lcoe_engine import PROFILE_DIR, calculate_system_lcoe

_COUNTRY_CODES = sorted(path.stem for path in PROFILE_DIR.glob("*.json"))
_REFERENCE_MIX = {
    "solar": 0.15, "wind_onshore": 0.10, "gas_ccgt": 0.30,
    "coal": 0.25, "nuclear": 0.18, "other": 0.02,
}


def test_at_least_the_core_countries_ship() -> None:
    assert len(_COUNTRY_CODES) >= 30
    assert {"KR", "US", "CN", "DE", "GB", "IN"}.issubset(_COUNTRY_CODES)


@pytest.mark.parametrize("code", _COUNTRY_CODES)
def test_country_profile_produces_sane_lcoe(code: str) -> None:
    result = calculate_system_lcoe(country=code, shares=_REFERENCE_MIX, carbon_price=50.0)
    assert 40.0 <= result["system_lcoe"] <= 350.0  # finite and in a plausible $/MWh band
    assert result["emission_intensity"] >= 0.0
