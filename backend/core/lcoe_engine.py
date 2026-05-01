from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

from backend.core.function_catalog import evaluate_function

PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "country_profiles"
VRE_GENERATORS = {"solar", "wind_onshore"}


def crf(discount_rate: float, lifetime_years: float) -> float:
    numerator = discount_rate * (1 + discount_rate) ** lifetime_years
    denominator = (1 + discount_rate) ** lifetime_years - 1
    return numerator / denominator


def load_country_profile(country_code: str) -> dict[str, Any]:
    profile_path = PROFILE_DIR / f"{country_code.upper()}.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"Country profile not found: {country_code}")
    return json.loads(profile_path.read_text())


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_shares(shares: dict[str, float]) -> tuple[dict[str, float], bool]:
    total = sum(max(value, 0.0) for value in shares.values())
    if total <= 0:
        raise ValueError("At least one generator share must be greater than zero.")
    normalized = {key: max(value, 0.0) / total for key, value in shares.items()}
    normalized_flag = abs(total - 1.0) > 0.001
    return normalized, normalized_flag


def _evaluate_configured_function(config: dict[str, Any], x_value: float) -> float:
    return float(
        evaluate_function(
            func_type=config["type"],
            params=config["params"],
            x=x_value,
            x_min=config.get("x_min"),
            x_max=config.get("x_max"),
        )
    )


def _generator_breakdown(
    generator_name: str,
    generator_config: dict[str, Any],
    share: float,
    vre_share: float,
    carbon_price: float,
    discount_rate: float,
) -> dict[str, float]:
    cf_eff = _evaluate_configured_function(generator_config["cf_eff_func"], vre_share)
    eta = _evaluate_configured_function(generator_config["eta_func"], cf_eff)
    eta_reference = float(generator_config.get("eta_reference", generator_config["eta_func"]["params"].get("a", eta)))
    efficiency_penalty = eta_reference / max(eta, 1e-6)
    capex = (
        generator_config["capex_usd_kw"]
        * crf(discount_rate, generator_config["lifetime_yr"])
        / (cf_eff * 8760)
        * 1000
    )
    fixed_opex = generator_config["opex_fixed_usd_kw_yr"] / (cf_eff * 8760) * 1000
    variable_opex = float(generator_config.get("opex_var_usd_mwh", 0.0))

    fuel = 0.0
    if "heat_rate_mmbtu_mwh" in generator_config and "fuel_usd_mmbtu" in generator_config:
        fuel = (
            generator_config["fuel_usd_mmbtu"]
            * generator_config["heat_rate_mmbtu_mwh"]
            * efficiency_penalty
        )

    emission_factor = float(generator_config.get("emission_factor_tco2_mwh", 0.0))
    carbon = carbon_price * emission_factor * efficiency_penalty
    integration = _evaluate_configured_function(generator_config["integration_cost_func"], share)

    return {
        "generator": generator_name,
        "cf_eff": cf_eff,
        "eta": eta,
        "capex": capex,
        "fixed_opex": fixed_opex,
        "variable_opex": variable_opex,
        "fuel": fuel,
        "carbon": carbon,
        "integration": integration,
        "total_lcoe": capex + fixed_opex + variable_opex + fuel + carbon + integration,
        "emission_intensity_tco2_mwh": emission_factor * efficiency_penalty,
    }


def _backup_flexibility(
    profile: dict[str, Any],
    normalized_shares: dict[str, float],
    vre_share: float,
) -> float:
    """Weighted flexibility of non-VRE backup generators (0 = all must-run, 1 = all dispatchable).

    Uses ``1 − variability_factor`` as each generator's flexibility score:
      - gas_ccgt  (VF=0.00) → flexibility 1.00  (fully dispatchable: can back off instantly)
      - coal       (VF=0.10) → flexibility 0.90  (slow-ramping, partial flexibility)
      - nuclear    (VF=0.20) → flexibility 0.80  (must-run baseload: cannot curtail output)
      - other      (VF=0.30) → flexibility 0.70

    When backup is inflexible (high must-run), VRE cannot be absorbed and must be curtailed
    even at moderate VRE shares.  When backup is fully dispatchable (gas), the backup simply
    backs off its output and curtailment is avoided.
    """
    non_vre_share = max(0.0, 1.0 - vre_share)
    if non_vre_share < 1e-6:
        return 1.0  # Pure VRE system — no backup; profile-only curtailment applies
    weighted = 0.0
    for gen, share in normalized_shares.items():
        if gen in VRE_GENERATORS or share <= 0:
            continue
        vf = float(profile["generators"][gen].get("variability_factor", 0.0))
        flexibility = 1.0 - vf
        weighted += (share / non_vre_share) * flexibility
    return max(0.0, min(1.0, weighted))


def _curtailment_metrics(
    profile: dict[str, Any],
    normalized_shares: dict[str, float],
    vre_share: float,
) -> dict[str, float]:
    """Curtailment is driven by two factors:

    1. **VRE share** — more VRE → more temporal surplus → more curtailment.
    2. **Backup flexibility** — dispatchable backup (gas) can back off and absorb VRE
       output; must-run backup (nuclear, coal) cannot → forces curtailment at the same VRE share.

    The model computes an ``effective_vre`` = VRE_share × flex_scale, where:
        flex_scale = 1 / backup_flexibility   (range ≈ 1.0 for all-gas to 1.25 for all-nuclear)

    The per-generator ``curtailment_func`` is evaluated at ``effective_vre`` rather than the
    raw VRE share, so grids with more must-run capacity see higher curtailment.
    """
    if vre_share <= 0:
        return {"curtailment_rate": 0.0, "curtailed_twh": 0.0, "backup_flexibility": 1.0}
    annual_twh = profile["annual_generation_twh"]

    # Backup flexibility: how well non-VRE generators can follow / back off
    backup_flex = _backup_flexibility(profile, normalized_shares, vre_share)
    # flex_scale > 1 when backup is inflexible (nuclear) → amplifies effective curtailment pressure
    flex_scale = 1.0 / max(backup_flex, 0.5)   # capped at 2× amplification
    effective_vre = min(1.0, vre_share * flex_scale)

    w_curtail = 0.0
    curtailed_twh = 0.0
    for gen in VRE_GENERATORS:
        share = normalized_shares.get(gen, 0.0)
        if share <= 0:
            continue
        gen_cfg = profile["generators"][gen]
        if "curtailment_func" in gen_cfg:
            cr = _evaluate_configured_function(gen_cfg["curtailment_func"], effective_vre)
            cr = max(0.0, min(1.0, cr))
        else:
            cf_base = float(gen_cfg.get("cf_base", 1.0))
            cf_eff = max(_evaluate_configured_function(gen_cfg["cf_eff_func"], vre_share), 1e-6)
            cr = max(0.0, 1.0 - cf_eff / cf_base)
        w_curtail += share * cr
        curtailed_twh += annual_twh * share * cr
    return {
        "curtailment_rate": w_curtail / vre_share,
        "curtailed_twh": curtailed_twh,
        "backup_flexibility": backup_flex,
    }


def _ess_metrics(
    profile: dict[str, Any],
    normalized_shares: dict[str, float],
    vre_share: float,
    ev_penetration: float = 0.0,
) -> dict[str, float]:
    """Compute ESS capacity and LCOE contribution split into short- and long-duration.

    Short-duration: absorbs curtailed VRE from each generator.
    Long-duration: last-gap seasonal storage activated above a VRE share threshold.
    """
    annual_twh = profile["annual_generation_twh"]
    ess = profile["ess"]
    short = ess["short_dur"]
    long = ess["long_dur"]

    # Short-duration: curtailment absorption per VRE generator
    throughput_gwh = 0.0
    for gen in VRE_GENERATORS:
        share = normalized_shares.get(gen, 0.0)
        if share <= 0:
            continue
        gen_cfg = profile["generators"][gen]
        if "curtailment_func" in gen_cfg:
            cr = _evaluate_configured_function(gen_cfg["curtailment_func"], vre_share)
            cr = max(0.0, min(1.0, cr))
        else:
            cf_base = float(gen_cfg.get("cf_base", 1.0))
            cf_eff = max(_evaluate_configured_function(gen_cfg["cf_eff_func"], vre_share), 1e-6)
            cr = max(0.0, 1.0 - cf_eff / cf_base)
        absorption = float(short.get(f"{gen}_absorption_fraction", 0.4))
        curtailed_gwh = share * annual_twh * 1000 * cr
        throughput_gwh += curtailed_gwh * absorption

    ev_offset = ev_penetration * short.get("ev_offset_gwh_per_unit", 0.0)
    net_throughput = max(throughput_gwh - ev_offset, 0.0)
    short_cycles = short["cycles_per_year"]
    short_dod = short["dod"]
    short_gwh = net_throughput / (short_cycles * short_dod) if (short_cycles * short_dod) > 0 else 0.0
    short_gw = short_gwh / float(short.get("duration_hr", 4.0))
    short_lcoe = (
        short["capex_usd_kwh"]
        * crf(profile["discount_rate"], short["lifetime_yr"])
        * short_gwh
        / annual_twh
    )

    # Long-duration: last-gap seasonal storage, VRE-share power law above threshold
    shifted = max(0.0, vre_share - float(long.get("threshold", 0.65)))
    long_ratio = _evaluate_configured_function(long["requirement_func"], shifted)
    long_gwh = (long_ratio * annual_twh * 1000) / (long["cycles_per_year"] * long["dod"])
    long_gw = long_gwh / float(long.get("duration_hr", 168.0))
    long_lcoe = (
        long["capex_usd_kwh"]
        * crf(profile["discount_rate"], long["lifetime_yr"])
        * long_gwh
        / annual_twh
    )

    return {
        "ess_requirement_gwh": short_gwh + long_gwh,
        "ess_requirement_gw": short_gw + long_gw,
        "ess_lcoe": short_lcoe + long_lcoe,
        "ess_short_gwh": short_gwh,
        "ess_short_gw": short_gw,
        "ess_short_lcoe": short_lcoe,
        "ess_long_gwh": long_gwh,
        "ess_long_gw": long_gw,
        "ess_long_lcoe": long_lcoe,
    }


def calculate_system_lcoe(
    country: str,
    shares: dict[str, float],
    carbon_price: float,
    ev_penetration: float = 0.0,
    annual_demand_twh: float | None = None,
    custom_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_profile = load_country_profile(country)
    profile = deep_merge(base_profile, custom_params or {})
    if annual_demand_twh is not None:
        profile["annual_generation_twh"] = annual_demand_twh
    normalized_shares, normalized = normalize_shares(shares)
    vre_share = sum(normalized_shares.get(key, 0.0) for key in VRE_GENERATORS)

    breakdowns: dict[str, dict[str, float]] = {}
    system_lcoe = 0.0
    emission_intensity = 0.0
    stack_components = {
        "capex": 0.0,
        "fixed_opex": 0.0,
        "variable_opex": 0.0,
        "fuel": 0.0,
        "carbon": 0.0,
        "integration": 0.0,
        "ess": 0.0,
    }

    for generator_name, share in normalized_shares.items():
        if share <= 0:
            breakdowns[generator_name] = {
                "generator": generator_name,
                "cf_eff": 0.0,
                "eta": 0.0,
                "capex": 0.0,
                "fixed_opex": 0.0,
                "variable_opex": 0.0,
                "fuel": 0.0,
                "carbon": 0.0,
                "integration": 0.0,
                "total_lcoe": 0.0,
                "emission_intensity_tco2_mwh": 0.0,
                "share_weighted_cost": 0.0,
            }
            continue

        generator_config = profile["generators"][generator_name]
        generator_breakdown = _generator_breakdown(
            generator_name=generator_name,
            generator_config=generator_config,
            share=share,
            vre_share=vre_share,
            carbon_price=carbon_price,
            discount_rate=profile["discount_rate"],
        )
        weighted_cost = share * generator_breakdown["total_lcoe"]
        generator_breakdown["share_weighted_cost"] = weighted_cost
        breakdowns[generator_name] = generator_breakdown

        system_lcoe += weighted_cost
        emission_intensity += share * generator_breakdown["emission_intensity_tco2_mwh"]
        for key in ("capex", "fixed_opex", "variable_opex", "fuel", "carbon", "integration"):
            stack_components[key] += share * generator_breakdown[key]

    ess_metrics = _ess_metrics(profile, normalized_shares, vre_share, ev_penetration)
    curtailment_metrics = _curtailment_metrics(profile, normalized_shares, vre_share)
    system_lcoe += ess_metrics["ess_lcoe"]
    stack_components["ess"] = ess_metrics["ess_lcoe"]
    annual_system_cost_usd_billion = system_lcoe * profile["annual_generation_twh"] / 1000
    annual_emissions_mtco2 = emission_intensity * profile["annual_generation_twh"]

    curve_data: list[dict[str, float]] = []
    base_non_vre = 1.0 - vre_share
    solar_ratio = normalized_shares.get("solar", 0.0) / vre_share if vre_share > 0 else 0.5
    wind_ratio = normalized_shares.get("wind_onshore", 0.0) / vre_share if vre_share > 0 else 0.5
    non_vre_weights = {
        key: (normalized_shares.get(key, 0.0) / base_non_vre if base_non_vre > 0 else 0.0)
        for key in normalized_shares
        if key not in VRE_GENERATORS
    }

    for vre_percent in range(0, 101):
        vre_point = vre_percent / 100
        curve_shares = {
            "solar": vre_point * solar_ratio,
            "wind_onshore": vre_point * wind_ratio,
        }
        residual = max(1.0 - vre_point, 0.0)
        for key, weight in non_vre_weights.items():
            curve_shares[key] = residual * weight
        for key in normalized_shares:
            curve_shares.setdefault(key, 0.0)

        # Guard: if no shares are positive (e.g. 100% VRE portfolio at vre_point=0),
        # substitute a tiny solar share so the curve point doesn't crash.
        if sum(max(v, 0.0) for v in curve_shares.values()) <= 0:
            curve_shares["solar"] = 1e-6

        point_result = calculate_system_lcoe_point(
            profile, curve_shares, carbon_price, ev_penetration,
            vre_share_override=vre_point,
        )
        curve_data.append(
            {
                "vre_share": vre_point,
                "system_lcoe": point_result["system_lcoe"],
                "emission_intensity": point_result["emission_intensity"],
                "ess_gwh": point_result["ess_requirement_gwh"],
                "ess_gw": point_result["ess_requirement_gw"],
                "capex": point_result["stack_components"]["capex"],
                "fuel": point_result["stack_components"]["fuel"],
                "carbon": point_result["stack_components"]["carbon"],
                "integration": point_result["stack_components"]["integration"],
                "ess": point_result["stack_components"]["ess"],
                "ess_short_gwh": point_result["ess_short_gwh"],
                "ess_long_gwh": point_result["ess_long_gwh"],
                "curtailment_rate": point_result["curtailment_rate"],
                "curtailed_twh": point_result["curtailed_twh"],
                "backup_flexibility": point_result["backup_flexibility"],
            }
        )

    return {
        "country": country.upper(),
        "shares": normalized_shares,
        "annual_demand_twh": profile["annual_generation_twh"],
        "system_lcoe": system_lcoe,
        "annual_system_cost_usd_billion": annual_system_cost_usd_billion,
        "lcoe_by_generator": breakdowns,
        "emission_intensity": emission_intensity,
        "annual_emissions_mtco2": annual_emissions_mtco2,
        "ess_requirement_gw": ess_metrics["ess_requirement_gw"],
        "ess_requirement_gwh": ess_metrics["ess_requirement_gwh"],
        "ess_short_gwh": ess_metrics["ess_short_gwh"],
        "ess_short_gw": ess_metrics["ess_short_gw"],
        "ess_short_lcoe": ess_metrics["ess_short_lcoe"],
        "ess_long_gwh": ess_metrics["ess_long_gwh"],
        "ess_long_gw": ess_metrics["ess_long_gw"],
        "ess_long_lcoe": ess_metrics["ess_long_lcoe"],
        "curtailment_rate": curtailment_metrics["curtailment_rate"],
        "curtailed_twh": curtailment_metrics["curtailed_twh"],
        "backup_flexibility": curtailment_metrics["backup_flexibility"],
        "curve_data": curve_data,
        "stack_components": stack_components,
        "data_quality": {
            "share_normalized": normalized,
            "used_custom_params": bool(custom_params),
            "custom_override_fields": sorted((custom_params or {}).keys()),
            "sources": profile.get("sources", []),
            "notes": [
                "ESS cost is modeled separately from generator LCOE.",
                "Shares are normalized if they do not sum to 1.0 within tolerance.",
                "Annual demand scales total cost, total emissions, and storage need estimates.",
            ],
        },
    }


def calculate_system_lcoe_point(
    profile: dict[str, Any],
    shares: dict[str, float],
    carbon_price: float,
    ev_penetration: float = 0.0,
    vre_share_override: float | None = None,
) -> dict[str, Any]:
    normalized_shares, _ = normalize_shares(shares)
    vre_share = (
        vre_share_override
        if vre_share_override is not None
        else sum(normalized_shares.get(key, 0.0) for key in VRE_GENERATORS)
    )
    system_lcoe = 0.0
    emission_intensity = 0.0
    stack_components = {
        "capex": 0.0,
        "fixed_opex": 0.0,
        "variable_opex": 0.0,
        "fuel": 0.0,
        "carbon": 0.0,
        "integration": 0.0,
        "ess": 0.0,
    }
    for generator_name, share in normalized_shares.items():
        if share <= 0:
            continue
        generator_breakdown = _generator_breakdown(
            generator_name=generator_name,
            generator_config=profile["generators"][generator_name],
            share=share,
            vre_share=vre_share,
            carbon_price=carbon_price,
            discount_rate=profile["discount_rate"],
        )
        system_lcoe += share * generator_breakdown["total_lcoe"]
        emission_intensity += share * generator_breakdown["emission_intensity_tco2_mwh"]
        for key in ("capex", "fixed_opex", "variable_opex", "fuel", "carbon", "integration"):
            stack_components[key] += share * generator_breakdown[key]

    ess_metrics = _ess_metrics(profile, normalized_shares, vre_share, ev_penetration)
    curtailment_metrics = _curtailment_metrics(profile, normalized_shares, vre_share)
    system_lcoe += ess_metrics["ess_lcoe"]
    stack_components["ess"] = ess_metrics["ess_lcoe"]

    return {
        "system_lcoe": system_lcoe,
        "emission_intensity": emission_intensity,
        "ess_requirement_gw": ess_metrics["ess_requirement_gw"],
        "ess_requirement_gwh": ess_metrics["ess_requirement_gwh"],
        "ess_short_gwh": ess_metrics["ess_short_gwh"],
        "ess_long_gwh": ess_metrics["ess_long_gwh"],
        "curtailment_rate": curtailment_metrics["curtailment_rate"],
        "curtailed_twh": curtailment_metrics["curtailed_twh"],
        "backup_flexibility": curtailment_metrics["backup_flexibility"],
        "stack_components": stack_components,
    }
