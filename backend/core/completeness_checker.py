from __future__ import annotations

from typing import Any


REQUIRED_SCALARS = (
    "capex_usd_kw",
    "opex_fixed_usd_kw_yr",
    "opex_var_usd_mwh",
    "lifetime_yr",
    "emission_factor_tco2_mwh",
)

REQUIRED_FUNCTIONS = ("cf_eff_func", "eta_func", "integration_cost_func")


def validate_generator_config(generator_config: dict[str, Any]) -> dict[str, Any]:
    components: dict[str, Any] = {}
    status = "complete"

    for key in REQUIRED_SCALARS:
        value = generator_config.get(key)
        component_status = "default" if value is not None else "missing"
        if component_status == "missing":
            status = "partial"
        components[key] = {
            "status": component_status,
            "r2": None,
            "source": generator_config.get("source", "default"),
        }

    for key in REQUIRED_FUNCTIONS:
        value = generator_config.get(key)
        component_status = "fitted" if value else "missing"
        if component_status == "missing":
            status = "partial"
        components[key] = {
            "status": component_status,
            "r2": value.get("r_squared") if isinstance(value, dict) else None,
            "source": value.get("source") if isinstance(value, dict) else None,
        }

    return {"status": status, "components": components}
