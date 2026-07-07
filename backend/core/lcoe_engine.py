from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

from backend.core.dispatch_engine import (
    VRE_GENERATORS as _DISPATCH_VRE,
    _base_capacity_factor,
    dispatch_hourly,
    run_dispatch_ensemble,
)
from backend.core.function_catalog import evaluate_function
from backend.core.hourly_profiles import EnsembleSettings, load_hourly_profiles, sample_ensemble

PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "country_profiles"
VRE_GENERATORS = {"solar", "wind_onshore"}

# ── Model constants ────────────────────────────────────────────────────────────
# These are the only numeric literals that are NOT read from country profiles.

# Round-trip efficiency of each storage tier (energy delivered ÷ energy stored),
# used when the profile does not specify `round_trip_efficiency`.
_SHORT_STORAGE_RTE: float = 0.85  # intraday lithium battery
_LONG_STORAGE_RTE: float = 0.45   # seasonal store (e.g. hydrogen)

# Fallback storage duration (hours) when neither the request nor the profile sets it.
_DEFAULT_SHORT_DURATION_HR: float = 4.0
_DEFAULT_LONG_DURATION_HR: float = 168.0

# Share normalisation tolerance: shares are considered already normalised when
# |sum − 1| ≤ this value (avoids floating-point noise triggering the flag).
_NORMALISATION_TOLERANCE: float = 0.001

# Numerical floor on effective capacity factor / efficiency to prevent ÷0.
_CF_FLOOR: float = 1e-6


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
    normalized_flag = abs(total - 1.0) > _NORMALISATION_TOLERANCE
    return normalized, normalized_flag


def _evaluate_configured_function(
    config: dict[str, Any],
    x_value: float,
    context: dict[str, float] | None = None,
) -> float:
    return float(
        evaluate_function(
            func_type=config["type"],
            params=config["params"],
            x=x_value,
            x_min=config.get("x_min"),
            x_max=config.get("x_max"),
            context=context,
        )
    )


def _resolve_x_value(
    config: dict[str, Any],
    default_x: float,
    context: dict[str, float],
) -> float:
    """Return the runtime x-value for a function config based on its ``x_variable`` field.

    If ``x_variable`` is absent or not in the context, falls back to ``default_x`` for
    backward compatibility.

    Available context keys:
      ``vre_share``        – system-wide VRE fraction
      ``own_share``        – this generator's portfolio share
      ``cf_eff``           – this generator's effective CF (computed before eta/integration)
      ``non_vre_share``    – 1 − vre_share
    """
    x_var = config.get("x_variable")
    if x_var and x_var in context:
        return context[x_var]
    return default_x


def _generator_breakdown(
    generator_name: str,
    generator_config: dict[str, Any],
    share: float,
    vre_share: float,
    carbon_price: float,
    discount_rate: float,
    cf_eff: float,
    itc_rate: float = 0.0,
    ptc_usd_mwh: float = 0.0,
) -> dict[str, float]:
    """Money math for one generator at its dispatch-realized capacity factor.

    ``cf_eff`` is the effective capacity factor that hourly merit-order dispatch
    actually produced for this generator — it is no longer inferred from a fitted
    ``cf_eff_func``. Curtailment and grid-integration effects are already priced by
    the dispatch (via realized CF) and by pattern-sized storage, so no separate
    ``integration_cost_func`` term is added here. The only retained behavioural
    curve is ``eta_func`` (thermal part-load efficiency), which the pattern cannot
    supply.
    """
    context: dict[str, float] = {
        "vre_share": vre_share,
        "own_share": share,
        "non_vre_share": max(0.0, 1.0 - vre_share),
        "cf_eff": max(cf_eff, _CF_FLOOR),
    }
    cf_eff = max(cf_eff, _CF_FLOOR)

    eta_x = _resolve_x_value(generator_config["eta_func"], cf_eff, context)
    eta = _evaluate_configured_function(generator_config["eta_func"], eta_x, context)
    eta_reference = float(generator_config.get("eta_reference", generator_config["eta_func"]["params"].get("a", eta)))
    efficiency_penalty = eta_reference / max(eta, 1e-6)
    # Investment tax credit (ITC): a fraction of capex is subsidised before annualisation.
    capex = (
        generator_config["capex_usd_kw"]
        * (1.0 - max(0.0, min(1.0, itc_rate)))
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
    # Production tax credit / feed-in tariff: a negative $/MWh subsidy on output.
    subsidy = -max(0.0, ptc_usd_mwh)

    return {
        "generator": generator_name,
        "cf_eff": cf_eff,
        "eta": eta,
        "capex": capex,
        "fixed_opex": fixed_opex,
        "variable_opex": variable_opex,
        "fuel": fuel,
        "carbon": carbon,
        "integration": 0.0,
        "subsidy": subsidy,
        "total_lcoe": capex + fixed_opex + variable_opex + fuel + carbon + subsidy,
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


def _build_storage_tiers(
    profile: dict[str, Any],
    ess_short_power_gw: float | None,
    ess_short_duration_hr: float | None,
    ess_long_power_gw: float | None,
    ess_long_duration_hr: float | None,
) -> list[dict[str, float]]:
    """Assemble the two user-set storage tiers for endogenous dispatch and costing.

    Power (GW) and duration (h) are user inputs (energy = power × duration); capex,
    lifetime, and round-trip efficiency come from the profile ``ess`` block (with
    fallbacks). Power defaults to 0 (no storage) when the caller does not set it.
    """
    ess = profile.get("ess", {})
    specs = (
        ("short", ess.get("short_dur", {}), ess_short_power_gw, ess_short_duration_hr,
         _DEFAULT_SHORT_DURATION_HR, _SHORT_STORAGE_RTE),
        ("long", ess.get("long_dur", {}), ess_long_power_gw, ess_long_duration_hr,
         _DEFAULT_LONG_DURATION_HR, _LONG_STORAGE_RTE),
    )
    tiers: list[dict[str, float]] = []
    for name, cfg, power, duration, default_duration, default_rte in specs:
        tiers.append({
            "name": name,
            "power_gw": float(power) if power is not None else 0.0,
            "duration_hr": float(duration) if duration is not None else float(cfg.get("duration_hr", default_duration)),
            "efficiency": float(cfg.get("round_trip_efficiency", default_rte)),
            "capex_usd_kwh": float(cfg.get("capex_usd_kwh", 0.0)),
            "lifetime_yr": float(cfg.get("lifetime_yr", 15.0)),
        })
    return tiers


def _ess_metrics(profile: dict[str, Any], storage_tiers: list[dict[str, float]]) -> dict[str, float]:
    """Annualised capital cost of the user-set storage tiers, $/MWh of system energy.

    Storage capacity is now exogenous (the user sets power and duration) and dispatched
    endogenously in ``dispatch_hourly`` — this function only prices it. Energy capacity
    is ``power × duration``; cost is ``capex × CRF × energy ÷ annual_generation``.
    """
    annual_twh = profile["annual_generation_twh"]
    discount = profile["discount_rate"]
    result: dict[str, float] = {"ess_requirement_gwh": 0.0, "ess_requirement_gw": 0.0, "ess_lcoe": 0.0}
    for tier in storage_tiers:
        name = str(tier["name"])
        power = float(tier.get("power_gw", 0.0))
        energy = power * float(tier.get("duration_hr", 0.0))
        lcoe = (
            float(tier.get("capex_usd_kwh", 0.0))
            * crf(discount, float(tier.get("lifetime_yr", 15.0)))
            * energy
            / annual_twh
        ) if annual_twh > 0 else 0.0
        result[f"ess_{name}_gwh"] = energy
        result[f"ess_{name}_gw"] = power
        result[f"ess_{name}_lcoe"] = lcoe
        result["ess_requirement_gwh"] += energy
        result["ess_requirement_gw"] += power
        result["ess_lcoe"] += lcoe
    for name in ("short", "long"):
        result.setdefault(f"ess_{name}_gwh", 0.0)
        result.setdefault(f"ess_{name}_gw", 0.0)
        result.setdefault(f"ess_{name}_lcoe", 0.0)
    return result


# ── Capacity expansion (screening-curve, to meet 100% load) ─────────────────────
_EXPANSION_MAX_ITERS: int = 8
_EXPANSION_UNSERVED_TOL_TWH: float = 0.02  # treat as "no unserved hour"
_EXPANSION_LEVELS: int = 120               # duration-curve slices for the screening sweep
_EXPANSION_STORAGE_STEPS: int = 14         # max increments when growing storage power
_HOURS_PER_YEAR: int = 8760


def _lcoe_at_cf(generator_config: dict[str, Any], cf: float, carbon_price: float, discount_rate: float) -> float:
    """Full LCOE ($/MWh) of a generator run at capacity factor ``cf``.

    Fixed costs (CAPEX·CRF, fixed O&M) spread over ``cf × 8760`` MWh — so LCOE rises as
    CF falls — plus the flat marginal terms. This is the screening-curve value used to
    pick the cheapest expandable technology for each slice of the unserved-duration curve.
    """
    cf = max(cf, 1e-3)
    energy_mwh = cf * _HOURS_PER_YEAR
    capex = generator_config["capex_usd_kw"] * crf(discount_rate, generator_config["lifetime_yr"]) / energy_mwh * 1000
    fixed = generator_config["opex_fixed_usd_kw_yr"] / energy_mwh * 1000
    variable = float(generator_config.get("opex_var_usd_mwh", 0.0))
    fuel = float(generator_config.get("fuel_usd_mmbtu", 0.0)) * float(generator_config.get("heat_rate_mmbtu_mwh", 0.0))
    carbon = carbon_price * float(generator_config.get("emission_factor_tco2_mwh", 0.0))
    return capex + fixed + variable + fuel + carbon


def _worst_case_profile(profile: dict[str, Any], sampled_profiles: list[Any], capacities: dict[str, float]) -> Any:
    """The sampled weather year with the highest net-load peak (demand − VRE).

    Sizing the expansion against this member so firm capacity covers the worst peak
    guarantees ~zero unserved across the whole ensemble, not just a representative year.
    """
    avg_load_gw = profile["annual_generation_twh"] * 1000 / _HOURS_PER_YEAR
    solar_cap = capacities.get("solar", 0.0)
    wind_cap = capacities.get("wind_onshore", 0.0)

    def net_load_peak(year_profile: Any) -> float:
        residual = year_profile.demand_norm * avg_load_gw - solar_cap * year_profile.solar_cf - wind_cap * year_profile.wind_cf
        return float(np.max(residual))

    return max(sampled_profiles, key=net_load_peak)


def _expand_to_meet_load(
    profile: dict[str, Any],
    year_profile: Any,
    base_capacities: dict[str, float],
    expandable: list[str],
    carbon_price: float,
    storage_tiers: list[dict[str, float]],
    annual_demand_twh: float,
) -> tuple[dict[str, float], list[dict[str, float]], str]:
    """Grow the checked generators (and, optionally, storage) to eliminate unserved energy.

    Two-stage, cheapest-first:

    1. **Storage** (if ``"storage"`` is checked) — grow the short-duration battery power to
       its useful plateau. Storage firms *recoverable* diurnal deficits (charge the midday
       surplus, discharge the evening peak); it stops helping once there is no more surplus
       to store or the deficit outlasts its duration.
    2. **Dispatchables** — screening-curve expansion on the residual unserved-duration curve:
       each firm slice goes to the generator with the lowest ``_lcoe_at_cf`` at that slice's
       capacity factor (peak → cheap-to-build peaker, sustained → cheap-to-run baseload).

    Re-dispatches after each move so VRE/storage feedback is captured. VRE cannot firm the
    peak, so it is never grown. Returns ``(added_by_key, grown_storage_tiers, note)`` where
    ``added_by_key`` may include a ``"storage"`` entry (added short-duration power, GW).
    """
    gens = profile["generators"]
    discount = profile["discount_rate"]
    expandable_disp = [g for g in expandable if g in gens and g not in _DISPATCH_VRE]
    tiers = [dict(tier) for tier in storage_tiers]
    short_tier = next((tier for tier in tiers if tier.get("name") == "short"), None)
    can_storage = "storage" in expandable and short_tier is not None
    added: dict[str, float] = {g: 0.0 for g in expandable_disp}
    capacities = {key: max(0.0, float(value)) for key, value in base_capacities.items()}

    if not expandable_disp and not can_storage:
        return added, tiers, (
            "Select a dispatchable generator or storage to expand — variable renewables "
            "alone cannot guarantee zero unserved hours."
        )

    def _dispatch_unserved(caps: dict[str, float], trs: list[dict[str, float]]) -> tuple[float, Any]:
        result = dispatch_hourly(
            profile=profile, year_profile=year_profile, shares=caps,
            annual_demand_twh=annual_demand_twh, carbon_price=carbon_price,
            capacities_gw=caps, storage_tiers=trs,
        )
        return float(np.sum(result.unserved_gw)) / 1000, result.unserved_gw

    unserved_twh, unserved = _dispatch_unserved(capacities, tiers)

    # 1. Storage first — grow short-duration power until it stops closing the gap.
    if can_storage and unserved_twh > _EXPANSION_UNSERVED_TOL_TWH:
        step = max(1.0, float(np.max(unserved)) / 8.0)
        for _ in range(_EXPANSION_STORAGE_STEPS):
            baseline = unserved_twh
            short_tier["power_gw"] = float(short_tier.get("power_gw", 0.0)) + step
            new_twh, new_unserved = _dispatch_unserved(capacities, tiers)
            if baseline - new_twh < 0.02 * baseline + 1e-6:  # saturated: revert the last step
                short_tier["power_gw"] = float(short_tier["power_gw"]) - step
                break
            added["storage"] = added.get("storage", 0.0) + step
            unserved_twh, unserved = new_twh, new_unserved
            if unserved_twh <= _EXPANSION_UNSERVED_TOL_TWH:
                break

    # 2. Dispatchable screening for the residual firm peak.
    for _ in range(_EXPANSION_MAX_ITERS):
        if unserved_twh <= _EXPANSION_UNSERVED_TOL_TWH or not expandable_disp:
            break
        unserved_sorted = np.sort(unserved)[::-1]
        peak = float(unserved_sorted[0])
        if peak <= 1e-6:
            break
        hours = len(unserved_sorted)
        slice_gw = peak / _EXPANSION_LEVELS
        for level in range(_EXPANSION_LEVELS):
            height = (level + 0.5) * slice_gw
            cf = max(float(np.count_nonzero(unserved_sorted > height)) / hours, 1e-3)
            best = min(expandable_disp, key=lambda g: _lcoe_at_cf(gens[g], cf, carbon_price, discount))
            # Nuclear runs as must-run baseload, so it delivers only cf_base of firm power.
            availability = _base_capacity_factor(gens[best], fallback=0.85) if best == "nuclear" else 1.0
            capacities[best] = capacities.get(best, 0.0) + slice_gw / max(availability, 0.1)
            added[best] += slice_gw / max(availability, 0.1)
        unserved_twh, unserved = _dispatch_unserved(capacities, tiers)

    note = ""
    if unserved_twh > _EXPANSION_UNSERVED_TOL_TWH and not expandable_disp:
        note = "Storage narrowed but could not fully close the gap — also expand a dispatchable generator to reach 100%."
    return added, tiers, note


def _coerce_ensemble_settings(ensemble: Any | None) -> EnsembleSettings:
    if ensemble is None:
        return EnsembleSettings()
    if isinstance(ensemble, EnsembleSettings):
        return ensemble
    if isinstance(ensemble, dict):
        return EnsembleSettings(
            method=ensemble.get("method", "jitter"),
            n_samples=int(ensemble.get("n_samples", 5)),
            sigma=float(ensemble.get("sigma", 0.04)),
            seed=int(ensemble.get("seed", 42)),
        )
    return EnsembleSettings(
        method=getattr(ensemble, "method", "jitter"),
        n_samples=int(getattr(ensemble, "n_samples", 5)),
        sigma=float(getattr(ensemble, "sigma", 0.04)),
        seed=int(getattr(ensemble, "seed", 42)),
    )


def _median_metric(summary: dict[str, Any], group: str, key: str) -> float:
    return float(summary["metrics"][group].get(key, {}).get("median", 0.0))


def _median_scalar(summary: dict[str, Any], key: str) -> float:
    return float(summary["metrics"]["scalars"].get(key, {}).get("median", 0.0))


# Generators eligible for the clean-energy subsidy (ITC / PTC) — the low-carbon set.
_CLEAN_GENERATORS = {"solar", "wind_onshore", "nuclear"}


def _calculate_from_dispatch_summary(
    profile: dict[str, Any],
    shares: dict[str, float],
    carbon_price: float,
    storage_tiers: list[dict[str, float]],
    dispatch_summary: dict[str, Any],
    subsidy_itc_pct: float = 0.0,
    subsidy_ptc_usd_mwh: float = 0.0,
) -> dict[str, Any]:
    normalized_shares, _ = normalize_shares(shares)
    vre_share = sum(normalized_shares.get(key, 0.0) for key in VRE_GENERATORS)

    all_generators = sorted(
        set(profile["generators"].keys()).union(normalized_shares.keys()),
        key=lambda name: ["solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "other", name].index(name)
        if name in {"solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "other"}
        else 99,
    )
    realized_weights = {
        gen: _median_metric(dispatch_summary, "realized_share", gen)
        for gen in all_generators
    }
    if sum(realized_weights.values()) <= 0:
        realized_weights = {gen: normalized_shares.get(gen, 0.0) for gen in all_generators}

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
        "subsidy": 0.0,
        "ess": 0.0,
    }

    for generator_name in all_generators:
        share = normalized_shares.get(generator_name, 0.0)
        realized_share = realized_weights.get(generator_name, 0.0)
        if generator_name not in profile["generators"] or (share <= 0 and realized_share <= 0):
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
                "subsidy": 0.0,
                "total_lcoe": 0.0,
                "emission_intensity_tco2_mwh": 0.0,
                "share_weighted_cost": 0.0,
                "realized_share": realized_share,
                "capacity_gw": _median_metric(dispatch_summary, "capacity_gw", generator_name),
                "capacity_share": _median_metric(dispatch_summary, "capacity_share", generator_name),
                "energy_twh": _median_metric(dispatch_summary, "energy_twh", generator_name),
            }
            continue

        eligible = generator_name in _CLEAN_GENERATORS
        generator_breakdown = _generator_breakdown(
            generator_name=generator_name,
            generator_config=profile["generators"][generator_name],
            share=share,
            vre_share=vre_share,
            carbon_price=carbon_price,
            discount_rate=profile["discount_rate"],
            cf_eff=_median_metric(dispatch_summary, "capacity_factor", generator_name),
            itc_rate=subsidy_itc_pct if eligible else 0.0,
            ptc_usd_mwh=subsidy_ptc_usd_mwh if eligible else 0.0,
        )
        weighted_cost = realized_share * generator_breakdown["total_lcoe"]
        generator_breakdown["share_weighted_cost"] = weighted_cost
        generator_breakdown["realized_share"] = realized_share
        generator_breakdown["capacity_gw"] = _median_metric(dispatch_summary, "capacity_gw", generator_name)
        generator_breakdown["capacity_share"] = _median_metric(dispatch_summary, "capacity_share", generator_name)
        generator_breakdown["energy_twh"] = _median_metric(dispatch_summary, "energy_twh", generator_name)
        breakdowns[generator_name] = generator_breakdown

        system_lcoe += weighted_cost
        emission_intensity += realized_share * generator_breakdown["emission_intensity_tco2_mwh"]
        for key in ("capex", "fixed_opex", "variable_opex", "fuel", "carbon", "integration", "subsidy"):
            stack_components[key] += realized_share * generator_breakdown[key]

    ess_metrics = _ess_metrics(profile, storage_tiers)
    system_lcoe += ess_metrics["ess_lcoe"]
    stack_components["ess"] = ess_metrics["ess_lcoe"]

    return {
        "shares": normalized_shares,
        "capacity_shares": {
            gen: _median_metric(dispatch_summary, "capacity_share", gen)
            for gen in all_generators
        },
        "capacities_gw": {
            gen: _median_metric(dispatch_summary, "capacity_gw", gen)
            for gen in all_generators
        },
        "system_lcoe": system_lcoe,
        "annual_system_cost_usd_billion": system_lcoe * profile["annual_generation_twh"] / 1000,
        "lcoe_by_generator": breakdowns,
        "emission_intensity": emission_intensity,
        "annual_emissions_mtco2": emission_intensity * profile["annual_generation_twh"],
        "ess_requirement_gw": ess_metrics["ess_requirement_gw"],
        "ess_requirement_gwh": ess_metrics["ess_requirement_gwh"],
        "ess_short_gwh": ess_metrics["ess_short_gwh"],
        "ess_short_gw": ess_metrics["ess_short_gw"],
        "ess_short_lcoe": ess_metrics["ess_short_lcoe"],
        "ess_long_gwh": ess_metrics["ess_long_gwh"],
        "ess_long_gw": ess_metrics["ess_long_gw"],
        "ess_long_lcoe": ess_metrics["ess_long_lcoe"],
        "curtailment_rate": _median_scalar(dispatch_summary, "curtailment_rate"),
        "curtailed_twh": _median_scalar(dispatch_summary, "curtailed_twh"),
        "unserved_twh": _median_scalar(dispatch_summary, "unserved_twh"),
        "backup_flexibility": _backup_flexibility(profile, normalized_shares, vre_share),
        "stack_components": stack_components,
    }


def _calculate_system_lcoe_dispatch(
    country: str,
    shares: dict[str, float],
    carbon_price: float,
    ev_penetration: float,
    annual_demand_twh: float | None,
    custom_params: dict[str, Any] | None,
    dispatch_mode: str,
    weather_years: list[int] | None,
    ensemble: Any | None,
    include_ldc: bool,
    capacities_gw: dict[str, float] | None,
    generator_order: list[str] | None = None,
    ess_short_power_gw: float | None = None,
    ess_short_duration_hr: float | None = None,
    ess_long_power_gw: float | None = None,
    ess_long_duration_hr: float | None = None,
    demand_pattern: str = "default",
    demand_peak_ratio: float | None = None,
    demand_monthly: list[float] | None = None,
    demand_daily: list[float] | None = None,
    expandable: list[str] | None = None,
    meet_full_load: bool = False,
    rps_target_share: float | None = None,
    rps_penalty_usd_mwh: float | None = None,
    subsidy_itc_pct: float | None = None,
    subsidy_ptc_usd_mwh: float | None = None,
) -> dict[str, Any]:
    base_profile = load_country_profile(country)
    profile = deep_merge(base_profile, custom_params or {})
    if annual_demand_twh is not None:
        profile["annual_generation_twh"] = annual_demand_twh

    storage_tiers = _build_storage_tiers(
        profile, ess_short_power_gw, ess_short_duration_hr, ess_long_power_gw, ess_long_duration_hr
    )
    settings = _coerce_ensemble_settings(ensemble)
    if capacities_gw:
        normalized_capacities = {key: max(0.0, float(value)) for key, value in capacities_gw.items()}
        total_capacity = sum(normalized_capacities.values())
        if total_capacity <= 0:
            raise ValueError("At least one generator capacity must be greater than zero.")
        normalized_shares = {
            key: normalized_capacities.get(key, 0.0) / total_capacity
            for key in profile["generators"]
        }
        normalized = False
    else:
        normalized_shares, normalized = normalize_shares(shares)
        normalized_capacities = None
    mode = "data" if dispatch_mode == "data" else "parametric"
    year_profiles = load_hourly_profiles(
        country=country,
        profile=profile,
        mode=mode,
        years=weather_years,
        seed=settings.seed,
        demand_pattern=demand_pattern,
        demand_peak_ratio=demand_peak_ratio,
        demand_monthly=demand_monthly,
        demand_daily=demand_daily,
    )

    # Capacity expansion: grow the checked generators to meet 100% of load, cheapest-first.
    expansion: dict[str, Any] | None = None
    if meet_full_load and expandable and normalized_capacities:
        # Size against the worst-case weather sample so 100% load holds across the ensemble.
        worst_profile = _worst_case_profile(
            profile, sample_ensemble(year_profiles, settings), normalized_capacities
        )
        added, storage_tiers, note = _expand_to_meet_load(
            profile=profile,
            year_profile=worst_profile,
            base_capacities=normalized_capacities,
            expandable=expandable,
            carbon_price=carbon_price,
            storage_tiers=storage_tiers,
            annual_demand_twh=profile["annual_generation_twh"],
        )
        normalized_capacities = {
            key: normalized_capacities.get(key, 0.0) + added.get(key, 0.0)
            for key in set(normalized_capacities) | set(added)
            if key in profile["generators"]
        }
        total_expanded = sum(normalized_capacities.values())
        if total_expanded > 0:
            normalized_shares = {
                key: normalized_capacities.get(key, 0.0) / total_expanded for key in profile["generators"]
            }
        expansion = {
            "requested": list(expandable),
            "added_capacities_gw": {key: round(value, 3) for key, value in added.items() if value > 1e-6},
            "note": note,
        }

    dispatch_summary = run_dispatch_ensemble(
        profile=profile,
        year_profiles=year_profiles,
        shares=normalized_shares,
        annual_demand_twh=profile["annual_generation_twh"],
        settings=settings,
        include_ldc=include_ldc,
        capacities_gw=normalized_capacities,
        generator_order=generator_order,
        carbon_price=carbon_price,
        storage_tiers=storage_tiers,
        return_members=True,
    )

    current = _calculate_from_dispatch_summary(
        profile=profile,
        shares=normalized_shares,
        carbon_price=carbon_price,
        storage_tiers=storage_tiers,
        dispatch_summary=dispatch_summary,
        subsidy_itc_pct=subsidy_itc_pct or 0.0,
        subsidy_ptc_usd_mwh=subsidy_ptc_usd_mwh or 0.0,
    )

    # Probabilistic band: recompute cost/emissions for each ensemble member so the
    # headline numbers carry the weather-uncertainty spread, not just a point estimate.
    member_lcoe: list[float] = []
    member_emis: list[float] = []
    for member in dispatch_summary.get("members", []):
        point = _calculate_from_dispatch_summary(
            profile=profile,
            shares=normalized_shares,
            carbon_price=carbon_price,
            storage_tiers=storage_tiers,
            dispatch_summary=member,
            subsidy_itc_pct=subsidy_itc_pct or 0.0,
            subsidy_ptc_usd_mwh=subsidy_ptc_usd_mwh or 0.0,
        )
        member_lcoe.append(point["system_lcoe"])
        member_emis.append(point["emission_intensity"])
    if member_lcoe:
        lcoe_p10, lcoe_p90 = (float(v) for v in np.quantile(member_lcoe, [0.1, 0.9]))
        emis_p10, emis_p90 = (float(v) for v in np.quantile(member_emis, [0.1, 0.9]))
        current["system_lcoe_p10"] = lcoe_p10
        current["system_lcoe_p90"] = lcoe_p90
        current["emission_intensity_p10"] = emis_p10
        current["emission_intensity_p90"] = emis_p90

    # Renewable-target (RPS) policy lever: compare achieved VRE generation share to the
    # target; optionally charge a shortfall (REC / alternative-compliance) penalty.
    rps: dict[str, Any] | None = None
    if rps_target_share is not None:
        achieved = sum(_median_metric(dispatch_summary, "realized_share", gen) for gen in VRE_GENERATORS)
        shortfall = max(0.0, float(rps_target_share) - achieved)
        penalty = float(rps_penalty_usd_mwh or 0.0) * shortfall
        if penalty > 0.0:
            current["system_lcoe"] += penalty
            current["annual_system_cost_usd_billion"] = (
                current["system_lcoe"] * profile["annual_generation_twh"] / 1000
            )
            current["stack_components"]["rps_penalty"] = penalty
            for key in ("system_lcoe_p10", "system_lcoe_p90"):
                if key in current:
                    current[key] += penalty
        rps = {
            "target_share": float(rps_target_share),
            "achieved_share": achieved,
            "met": achieved >= float(rps_target_share) - 1e-4,
            "shortfall_share": shortfall,
            "penalty_lcoe": penalty,
        }

    # The 0→100% VRE-share sweep (curve_data) was an arbitrary interpolated path used
    # only by sensitivity sub-charts that have been removed. Dropped to avoid re-running
    # the full dispatch ensemble 101× per request. Results describe the chosen mix only.
    curve_data: list[dict[str, float]] = []

    source_note = "Hourly profiles are generated from the parametric synthesizer."
    if dispatch_mode == "data":
        if all(str(source).startswith("parametric") for source in dispatch_summary["ensemble"]["sources"]):
            source_note = "No hourly data files were found; data mode fell back to seeded parametric profiles."
        else:
            source_note = "Hourly profiles were loaded from backend/data/hourly."

    result = {
        "country": country.upper(),
        "annual_demand_twh": profile["annual_generation_twh"],
        **current,
        "curve_data": curve_data,
        "dispatch": {
            "mode": mode,
            "ensemble": dispatch_summary["ensemble"],
            "metrics": dispatch_summary["metrics"],
        },
        "data_quality": {
            "share_normalized": normalized,
            "used_custom_params": bool(custom_params),
            "custom_override_fields": sorted((custom_params or {}).keys()),
            "sources": profile.get("sources", []),
            "notes": [
                "System cost, emissions, curtailment, storage, and capacity factors are derived from hourly merit-order dispatch.",
                "Shares are normalized if they do not sum to 1.0 within tolerance.",
                "Annual demand scales total cost, total emissions, and storage need estimates.",
                source_note,
            ],
        },
    }
    if include_ldc and "ldc" in dispatch_summary:
        result["ldc"] = dispatch_summary["ldc"]
    if include_ldc and "chronological" in dispatch_summary:
        result["chronological"] = dispatch_summary["chronological"]
    if expansion is not None:
        result["expansion"] = expansion
    if rps is not None:
        result["rps"] = rps
    return result


def calculate_system_lcoe(
    country: str,
    shares: dict[str, float],
    carbon_price: float,
    ev_penetration: float = 0.0,
    annual_demand_twh: float | None = None,
    custom_params: dict[str, Any] | None = None,
    dispatch_mode: str = "parametric",
    weather_years: list[int] | None = None,
    ensemble: Any | None = None,
    include_ldc: bool = False,
    capacities_gw: dict[str, float] | None = None,
    generator_order: list[str] | None = None,
    ess_short_power_gw: float | None = None,
    ess_short_duration_hr: float | None = None,
    ess_long_power_gw: float | None = None,
    ess_long_duration_hr: float | None = None,
    demand_pattern: str = "default",
    demand_peak_ratio: float | None = None,
    demand_monthly: list[float] | None = None,
    demand_daily: list[float] | None = None,
    expandable: list[str] | None = None,
    meet_full_load: bool = False,
    rps_target_share: float | None = None,
    rps_penalty_usd_mwh: float | None = None,
    subsidy_itc_pct: float | None = None,
    subsidy_ptc_usd_mwh: float | None = None,
) -> dict[str, Any]:
    return _calculate_system_lcoe_dispatch(
        country=country,
        shares=shares,
        carbon_price=carbon_price,
        ev_penetration=ev_penetration,
        annual_demand_twh=annual_demand_twh,
        custom_params=custom_params,
        dispatch_mode=dispatch_mode,
        weather_years=weather_years,
        ensemble=ensemble,
        include_ldc=include_ldc,
        capacities_gw=capacities_gw,
        generator_order=generator_order,
        ess_short_power_gw=ess_short_power_gw,
        ess_short_duration_hr=ess_short_duration_hr,
        ess_long_power_gw=ess_long_power_gw,
        ess_long_duration_hr=ess_long_duration_hr,
        demand_pattern=demand_pattern,
        demand_peak_ratio=demand_peak_ratio,
        demand_monthly=demand_monthly,
        demand_daily=demand_daily,
        expandable=expandable,
        meet_full_load=meet_full_load,
        rps_target_share=rps_target_share,
        rps_penalty_usd_mwh=rps_penalty_usd_mwh,
        subsidy_itc_pct=subsidy_itc_pct,
        subsidy_ptc_usd_mwh=subsidy_ptc_usd_mwh,
    )
