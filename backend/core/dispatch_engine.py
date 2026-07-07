from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from backend.core.hourly_profiles import HOURS_PER_YEAR, EnsembleSettings, YearProfile

VRE_GENERATORS = ("solar", "wind_onshore")
DISPLAY_ORDER = ("solar", "wind_onshore", "nuclear", "coal", "gas_ccgt", "other")
QUANTILES = (0.1, 0.5, 0.9)


@dataclass(frozen=True)
class DispatchResult:
    country: str
    year: int
    source: str
    annual_demand_twh: float
    demand_gw: np.ndarray
    dispatch_gw: dict[str, np.ndarray]
    available_gw: dict[str, np.ndarray]
    curtailed_gw: dict[str, np.ndarray]
    unserved_gw: np.ndarray
    capacities_gw: dict[str, float]


def _simulate_storage_soc(
    surplus_gw: np.ndarray,
    deficit_gw: np.ndarray,
    power_gw: float,
    energy_gwh: float,
    efficiency: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Chronological state-of-charge dispatch of one storage device.

    Walks the 8760-hour surplus/deficit signal in order, charging from surplus and
    discharging to deficit within the device's power and energy limits. This is what
    makes storage *endogenous*: the user sets power (GW) and duration (h) — energy =
    power × duration — and the model decides how much it actually shifts, reducing
    curtailment (via charging) and unserved energy (via discharging).

    Args:
        surplus_gw: Hourly chargeable surplus (curtailed generation), GW.
        deficit_gw: Hourly dischargeable need (unserved demand), GW.
        power_gw: Rated charge/discharge power, GW.
        energy_gwh: Usable energy capacity (= power × duration), GWh.
        efficiency: Round-trip efficiency (energy delivered ÷ energy stored), 0–1.

    Returns:
        ``(charge_gw, discharge_gw)`` hourly arrays — energy drawn from surplus and
        energy delivered to deficit respectively.

    Algorithm:
        Greedy causal loop with state of charge ``soc``:
        charge ``c = min(surplus, power, energy - soc)`` → ``soc += c``;
        discharge ``d = min(deficit, power, soc·η)`` → ``soc -= d/η``.
    """
    hours = len(surplus_gw)
    charge = np.zeros(hours, dtype=float)
    discharge = np.zeros(hours, dtype=float)
    if power_gw <= 0.0 or energy_gwh <= 0.0:
        return charge, discharge

    soc = 0.0
    eff = max(1e-6, float(efficiency))
    for h in range(hours):
        if surplus_gw[h] > 1e-9 and soc < energy_gwh:
            c = min(float(surplus_gw[h]), power_gw, energy_gwh - soc)
            charge[h] = c
            soc += c
        elif deficit_gw[h] > 1e-9 and soc > 1e-9:
            d = min(float(deficit_gw[h]), power_gw, soc * eff)
            discharge[h] = d
            soc -= d / eff
    return charge, discharge


def _marginal_cost_usd_mwh(generator_config: dict[str, Any], carbon_price: float) -> float:
    """Short-run (dispatch) marginal cost of a flexible generator, $/MWh.

    This is the *ordering* cost for the merit stack — variable O&M plus fuel plus the
    carbon charge — so a rising carbon price can reorder the stack (e.g. gas overtaking
    coal once its lower emission factor outweighs its higher fuel cost). Capital and
    fixed O&M are sunk for dispatch and excluded here (they still enter the LCOE).

    Algorithm:
        $$mc = opex_{var} + fuel_{\\$/mmbtu}\\cdot HR_{mmbtu/MWh} + p_{CO_2}\\cdot ef_{tCO_2/MWh}$$
        ASCII: mc = opex_var + fuel_price*heat_rate + carbon_price*emission_factor
    """
    variable = float(generator_config.get("opex_var_usd_mwh", 0.0))
    fuel = 0.0
    if "heat_rate_mmbtu_mwh" in generator_config and "fuel_usd_mmbtu" in generator_config:
        fuel = float(generator_config["fuel_usd_mmbtu"]) * float(generator_config["heat_rate_mmbtu_mwh"])
    carbon = float(carbon_price) * float(generator_config.get("emission_factor_tco2_mwh", 0.0))
    return variable + fuel + carbon


def dispatch_hourly(
    profile: dict[str, Any],
    year_profile: YearProfile,
    shares: dict[str, float],
    annual_demand_twh: float,
    carbon_price: float = 0.0,
    capacities_gw: dict[str, float] | None = None,
    generator_order: list[str] | None = None,
    storage_tiers: list[dict[str, float]] | None = None,
) -> DispatchResult:
    """Screening merit-order dispatch over the hourly (net-load) pattern.

    Stacking order, bottom to top:

    1. **Nuclear must-run baseload** — runs flat at its base capacity factor (the
       "minimum capacity factor of nuclear" rule); curtails only if it alone exceeds
       demand. Non-dispatchable policy baseload.
    2. **VRE priority** — solar/wind serve the post-nuclear residual at ~zero marginal
       cost; the surplus is curtailed.
    3. **Flexible thermals** — coal/gas/other fill the remaining residual in ascending
       ``_marginal_cost_usd_mwh`` order, so the merit stack responds to carbon price.
       An explicit ``generator_order`` overrides this with a manual merit order.

    This replaces a fixed hand-ordered greedy stack; the annual energy per generator is
    the area under its slice of the net-load duration curve.
    """
    generator_names = _ordered_generators(profile, generator_order)
    normalized_shares = _normalize_shares(shares)
    fixed_capacities = _normalize_capacities(capacities_gw or {})
    hours = len(year_profile.demand_norm)
    if hours != HOURS_PER_YEAR:
        raise ValueError("Dispatch requires an 8760-hour profile.")

    average_load_gw = annual_demand_twh * 1000 / hours
    demand_gw = year_profile.demand_norm * average_load_gw

    capacities_gw = {name: 0.0 for name in generator_names}
    dispatch_gw = {name: np.zeros(hours, dtype=float) for name in generator_names}
    available_gw = {name: np.zeros(hours, dtype=float) for name in generator_names}
    curtailed_gw = {name: np.zeros(hours, dtype=float) for name in generator_names}

    for gen in generator_names:
        if gen in VRE_GENERATORS:
            cf = year_profile.solar_cf if gen == "solar" else year_profile.wind_cf
            if gen in fixed_capacities:
                capacities_gw[gen] = fixed_capacities[gen]
            else:
                mean_cf = max(float(np.mean(cf)), 1e-6)
                target_gwh = annual_demand_twh * 1000 * normalized_shares.get(gen, 0.0)
                capacities_gw[gen] = target_gwh / (mean_cf * hours) if target_gwh > 0 else 0.0
            available_gw[gen] = capacities_gw[gen] * cf
            continue

        if gen == "nuclear":
            nuclear_cf = _base_capacity_factor(profile["generators"][gen], fallback=0.85)
            if gen in fixed_capacities:
                capacities_gw[gen] = fixed_capacities[gen]
            else:
                gen_share = normalized_shares.get(gen, 0.0)
                target_gwh = annual_demand_twh * 1000 * gen_share
                capacities_gw[gen] = target_gwh / (nuclear_cf * hours) if target_gwh > 0 else 0.0
            available_gw[gen] = np.full(hours, capacities_gw[gen] * nuclear_cf, dtype=float)
            continue

        gen_cf = _base_capacity_factor(profile["generators"][gen], fallback=0.55)
        if gen in fixed_capacities:
            capacities_gw[gen] = fixed_capacities[gen]
        else:
            gen_share = normalized_shares.get(gen, 0.0)
            target_gwh = annual_demand_twh * 1000 * gen_share
            capacities_gw[gen] = target_gwh / (gen_cf * hours) if target_gwh > 0 else 0.0
        available_gw[gen] = np.full(hours, capacities_gw[gen], dtype=float)

    vre_names = [gen for gen in generator_names if gen in VRE_GENERATORS]
    flexible_names = [gen for gen in generator_names if gen not in VRE_GENERATORS and gen != "nuclear"]
    if generator_order:
        # Manual merit override (advanced): order flexibles by the given sequence.
        priority = {gen: index for index, gen in enumerate(generator_order)}
        flexible_names.sort(key=lambda gen: priority.get(gen, len(priority)))
    else:
        # Default: ascending short-run marginal cost, so carbon price reorders the stack.
        flexible_names.sort(
            key=lambda gen: _marginal_cost_usd_mwh(profile["generators"][gen], carbon_price)
        )

    residual_gw = demand_gw.copy()

    # 1. Nuclear must-run baseload: runs flat, curtails only when it alone tops demand.
    if "nuclear" in generator_names:
        must_run_gw = available_gw["nuclear"]
        curtailed_gw["nuclear"] = np.maximum(must_run_gw - demand_gw, 0.0)
        dispatch_gw["nuclear"] = must_run_gw - curtailed_gw["nuclear"]
        residual_gw = np.maximum(residual_gw - dispatch_gw["nuclear"], 0.0)

    # 2. VRE priority: serve the residual, curtail the surplus (allocated pro-rata).
    vre_available_gw = sum((available_gw[gen] for gen in vre_names), np.zeros(hours, dtype=float))
    served_vre_gw = np.minimum(residual_gw, vre_available_gw)
    with np.errstate(divide="ignore", invalid="ignore"):
        served_fraction = np.where(vre_available_gw > 0.0, served_vre_gw / vre_available_gw, 0.0)
    for gen in vre_names:
        dispatch_gw[gen] = available_gw[gen] * served_fraction
        curtailed_gw[gen] = available_gw[gen] - dispatch_gw[gen]
    residual_gw = np.maximum(residual_gw - served_vre_gw, 0.0)

    # 3. Flexible thermals fill the residual in merit order.
    for gen in flexible_names:
        dispatch_gw[gen] = np.minimum(residual_gw, available_gw[gen])
        residual_gw = np.maximum(residual_gw - dispatch_gw[gen], 0.0)

    unserved_gw = residual_gw

    # 4. Endogenous storage (user-set tiers): charge from curtailment, discharge to
    #    unserved demand, in tier order (short/intraday first, then long/seasonal).
    if storage_tiers:
        curtailed_total = sum(curtailed_gw.values(), np.zeros(hours, dtype=float))
        surplus_gw = curtailed_total.copy()
        deficit_gw = unserved_gw.copy()
        for tier in storage_tiers:
            power = float(tier.get("power_gw", 0.0))
            energy = power * float(tier.get("duration_hr", 0.0))
            charge, discharge = _simulate_storage_soc(
                surplus_gw, deficit_gw, power, energy, float(tier.get("efficiency", 0.85))
            )
            surplus_gw = surplus_gw - charge
            deficit_gw = deficit_gw - discharge
        # Attribute the absorbed surplus back to each generator's curtailment pro-rata.
        with np.errstate(divide="ignore", invalid="ignore"):
            remaining_fraction = np.where(curtailed_total > 0.0, surplus_gw / curtailed_total, 0.0)
        for gen in curtailed_gw:
            curtailed_gw[gen] = curtailed_gw[gen] * remaining_fraction
        unserved_gw = np.maximum(deficit_gw, 0.0)

    return DispatchResult(
        country=year_profile.country,
        year=year_profile.year,
        source=year_profile.source,
        annual_demand_twh=annual_demand_twh,
        demand_gw=demand_gw,
        dispatch_gw=dispatch_gw,
        available_gw=available_gw,
        curtailed_gw=curtailed_gw,
        unserved_gw=unserved_gw,
        capacities_gw=capacities_gw,
    )


def run_dispatch_ensemble(
    profile: dict[str, Any],
    year_profiles: list[YearProfile],
    shares: dict[str, float],
    annual_demand_twh: float,
    settings: EnsembleSettings | None = None,
    include_ldc: bool = False,
    capacities_gw: dict[str, float] | None = None,
    generator_order: list[str] | None = None,
    carbon_price: float = 0.0,
    storage_tiers: list[dict[str, float]] | None = None,
    return_members: bool = False,
) -> dict[str, Any]:
    from backend.core.hourly_profiles import sample_ensemble

    sampled_profiles = sample_ensemble(year_profiles, settings)
    results = [
        dispatch_hourly(
            profile=profile,
            year_profile=year_profile,
            shares=shares,
            annual_demand_twh=annual_demand_twh,
            carbon_price=carbon_price,
            capacities_gw=capacities_gw,
            generator_order=generator_order,
            storage_tiers=storage_tiers,
        )
        for year_profile in sampled_profiles
    ]
    summary = aggregate_dispatch_results(results, settings=settings, include_ldc=include_ldc)
    if return_members:
        # Per-member summaries (median = that member's value) so the caller can build a
        # cost/emissions distribution across the ensemble, not just a point estimate.
        summary["members"] = [
            aggregate_dispatch_results([result], settings=settings, include_ldc=False)
            for result in results
        ]
    return summary


def aggregate_dispatch_results(
    results: list[DispatchResult],
    settings: EnsembleSettings | None = None,
    include_ldc: bool = False,
) -> dict[str, Any]:
    if not results:
        raise ValueError("At least one dispatch result is required.")

    generator_names = _ordered_result_generators(results[0])
    per_result_metrics = [_dispatch_metrics(result, generator_names) for result in results]
    scalar_names = (
        "curtailment_rate",
        "curtailed_twh",
        "unserved_twh",
        "served_twh",
        "residual_peak_gw",
        "peak_load_gw",
    )
    grouped_metric_names = (
        "capacity_factor",
        "realized_share",
        "energy_twh",
        "capacity_gw",
        "capacity_share",
        "curtailment_rate_by_generator",
    )

    scalars = {
        name: _quantile_summary([metrics["scalars"][name] for metrics in per_result_metrics])
        for name in scalar_names
    }
    grouped = {
        group: {
            gen: _quantile_summary([metrics[group][gen] for metrics in per_result_metrics])
            for gen in generator_names
        }
        for group in grouped_metric_names
    }

    output: dict[str, Any] = {
        "country": results[0].country,
        "annual_demand_twh": results[0].annual_demand_twh,
        "ensemble": {
            "method": (settings.method if settings else "single"),
            "n_samples": len(results),
            "sigma": (settings.sigma if settings else 0.0),
            "seed": (settings.seed if settings else 0),
            "sources": sorted({result.source for result in results}),
            "years": [result.year for result in results],
        },
        "metrics": {
            **grouped,
            "scalars": scalars,
        },
    }

    if include_ldc:
        output["ldc"] = _aggregate_ldc(results, generator_names)
        output["chronological"] = _chronological_series(results[0], generator_names)

    return output


def _dispatch_metrics(result: DispatchResult, generator_names: list[str]) -> dict[str, Any]:
    hours = len(result.demand_gw)
    energy_twh: dict[str, float] = {}
    capacity_factor: dict[str, float] = {}
    realized_share: dict[str, float] = {}
    capacity_gw: dict[str, float] = {}
    capacity_share: dict[str, float] = {}
    curtailment_by_generator: dict[str, float] = {}

    total_served_twh = 0.0
    for gen in generator_names:
        energy = float(np.sum(result.dispatch_gw.get(gen, np.zeros(hours)))) / 1000
        energy_twh[gen] = energy
        total_served_twh += energy
        capacity = float(result.capacities_gw.get(gen, 0.0))
        capacity_gw[gen] = capacity
        capacity_factor[gen] = energy * 1000 / (capacity * hours) if capacity > 0 else 0.0
        available = float(np.sum(result.available_gw.get(gen, np.zeros(hours)))) / 1000
        curtailed = float(np.sum(result.curtailed_gw.get(gen, np.zeros(hours)))) / 1000
        curtailment_by_generator[gen] = curtailed / available if available > 0 else 0.0

    total_capacity_gw = sum(capacity_gw.values())
    for gen in generator_names:
        realized_share[gen] = energy_twh[gen] / total_served_twh if total_served_twh > 0 else 0.0
        capacity_share[gen] = capacity_gw[gen] / total_capacity_gw if total_capacity_gw > 0 else 0.0

    total_vre_available_twh = sum(
        float(np.sum(result.available_gw.get(gen, np.zeros(hours)))) / 1000 for gen in VRE_GENERATORS
    )
    total_vre_curtailed_twh = sum(
        float(np.sum(result.curtailed_gw.get(gen, np.zeros(hours)))) / 1000 for gen in VRE_GENERATORS
    )
    vre_dispatch = sum((result.dispatch_gw.get(gen, np.zeros(hours)) for gen in VRE_GENERATORS), np.zeros(hours))
    net_load = np.maximum(result.demand_gw - vre_dispatch, 0.0)
    unserved_twh = float(np.sum(result.unserved_gw)) / 1000

    return {
        "capacity_factor": capacity_factor,
        "realized_share": realized_share,
        "energy_twh": energy_twh,
        "capacity_gw": capacity_gw,
        "capacity_share": capacity_share,
        "curtailment_rate_by_generator": curtailment_by_generator,
        "scalars": {
            "curtailment_rate": (
                total_vre_curtailed_twh / total_vre_available_twh if total_vre_available_twh > 0 else 0.0
            ),
            "curtailed_twh": total_vre_curtailed_twh,
            "unserved_twh": unserved_twh,
            "served_twh": total_served_twh,
            "residual_peak_gw": float(np.max(net_load)) if len(net_load) else 0.0,
            "peak_load_gw": float(np.max(result.demand_gw)) if len(result.demand_gw) else 0.0,
        },
    }


def _aggregate_ldc(results: list[DispatchResult], generator_names: list[str]) -> dict[str, Any]:
    hours = len(results[0].demand_gw)
    per_result_series = [_ldc_series(result, generator_names) for result in results]
    keys = list(per_result_series[0].keys())
    series = {
        key: _quantile_array([item[key] for item in per_result_series])
        for key in keys
    }
    return {
        "x_hours": [float(value) for value in range(1, hours + 1)],
        "x_percent": [float(value) for value in np.linspace(0, 100, hours)],
        "series": series,
        "resource_order": generator_names,
    }


def _ldc_series(result: DispatchResult, generator_names: list[str]) -> dict[str, np.ndarray]:
    hours = len(result.demand_gw)
    vre_dispatch = sum((result.dispatch_gw.get(gen, np.zeros(hours)) for gen in VRE_GENERATORS), np.zeros(hours))
    net_load = np.maximum(result.demand_gw - vre_dispatch, 0.0)
    order = np.argsort(-result.demand_gw)
    served_load = sum((result.dispatch_gw.get(gen, np.zeros(hours)) for gen in generator_names), np.zeros(hours))

    series: dict[str, np.ndarray] = {
        "demand": result.demand_gw[order],
        "net_load": net_load[order],
        "served_load": served_load[order],
        "curtailed_vre": sum(
            (result.curtailed_gw.get(gen, np.zeros(hours)) for gen in VRE_GENERATORS),
            np.zeros(hours),
        )[order],
        "unserved": result.unserved_gw[order],
    }
    for gen in generator_names:
        series[gen] = result.dispatch_gw.get(gen, np.zeros(hours))[order]
    return series


def _quantile_array(values: list[np.ndarray]) -> dict[str, list[float]]:
    stacked = np.vstack(values)
    p10, median, p90 = np.quantile(stacked, QUANTILES, axis=0)
    return {
        "p10": _round_list(p10),
        "median": _round_list(median),
        "p90": _round_list(p90),
    }


def _quantile_summary(values: list[float]) -> dict[str, float]:
    p10, median, p90 = np.quantile(np.asarray(values, dtype=float), QUANTILES)
    return {
        "p10": float(p10),
        "median": float(median),
        "p90": float(p90),
    }


def _round_list(values: np.ndarray, decimals: int = 6) -> list[float]:
    return [float(value) for value in np.round(values.astype(float), decimals)]


def _chronological_series(result: DispatchResult, generator_names: list[str]) -> dict[str, Any]:
    """Hour-by-hour (chronological, not sorted) dispatch of one representative member.

    Unlike the load-duration curve this preserves the time axis, so the frontend can
    show the actual 8760-hour generation mix (day/night and seasonal cycles). Values
    are GW, rounded to 0.01 GW to keep the payload compact.
    """
    hours = len(result.demand_gw)
    series: dict[str, list[float]] = {
        gen: _round_list(result.dispatch_gw.get(gen, np.zeros(hours)), 2) for gen in generator_names
    }
    series["demand"] = _round_list(result.demand_gw, 2)
    series["curtailed"] = _round_list(
        sum((result.curtailed_gw.get(gen, np.zeros(hours)) for gen in generator_names), np.zeros(hours)), 2
    )
    series["unserved"] = _round_list(result.unserved_gw, 2)
    return {
        "hours": list(range(hours)),
        "series": series,
        "resource_order": generator_names,
    }


def _ordered_generators(profile: dict[str, Any], generator_order: list[str] | None = None) -> list[str]:
    available = set(profile["generators"].keys())
    preferred = generator_order or list(DISPLAY_ORDER)
    ordered = [gen for gen in preferred if gen in available]
    ordered.extend(sorted(available.difference(ordered)))
    return ordered


def _ordered_result_generators(result: DispatchResult) -> list[str]:
    available = set(result.dispatch_gw.keys())
    return [gen for gen in result.dispatch_gw.keys() if gen in available]


def _normalize_shares(shares: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(value)) for value in shares.values())
    if total <= 0:
        raise ValueError("At least one generator share must be greater than zero.")
    return {key: max(0.0, float(value)) / total for key, value in shares.items()}


def _normalize_capacities(capacities_gw: dict[str, float]) -> dict[str, float]:
    return {key: max(0.0, float(value)) for key, value in capacities_gw.items()}


def _base_capacity_factor(generator_config: dict[str, Any], fallback: float) -> float:
    if "cf_base" in generator_config:
        return max(float(generator_config["cf_base"]), 1e-6)
    func = generator_config.get("cf_eff_func", {})
    if func.get("type") == "constant":
        return max(float(func.get("params", {}).get("a", fallback)), 1e-6)
    return max(float(fallback), 1e-6)
