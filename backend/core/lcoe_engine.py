from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

from backend.core.dispatch_engine import (
    VRE_GENERATORS as _DISPATCH_VRE,
    _marginal_cost_usd_mwh,
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


# ── Capacity expansion (least firm-cost, to meet 100% load) ─────────────────────
_EXPANSION_UNSERVED_TOL_TWH: float = 0.02   # treat as "no unserved hour"
_EXPANSION_MAX_STEPS: int = 90              # greedy increments before giving up
_EXPANSION_STEP_DIVISOR: float = 12.0       # base increment size = initial unserved peak ÷ this
_EXPANSION_MAX_STEP_DOUBLINGS: int = 12     # escalate the step this many times to cross VRE thresholds
_EXPANSION_TRIM_ITERS: int = 8             # bisection sweeps to shave overshoot off the last move
_DEFAULT_STORAGE_LIFETIME_YR: float = 15.0
_HOURS_PER_YEAR: int = 8760


def _annual_fixed_cost_gen(generator_config: dict[str, Any], discount_rate: float, gw: float) -> float:
    """Annualised fixed cost ($/yr) of ``gw`` GW of a generator: CAPEX·CRF + fixed O&M.

    This is the *firm-capacity* cost — what it costs to have the plant standing, independent
    of how much energy it runs. Running (fuel + carbon) cost is a dispatch outcome and is not
    part of the build decision; it enters the reported LCOE via the merit-order dispatch.
    """
    crf_value = crf(discount_rate, generator_config["lifetime_yr"])
    return 1e6 * gw * (
        generator_config["capex_usd_kw"] * crf_value + float(generator_config.get("opex_fixed_usd_kw_yr", 0.0))
    )


def _annual_fixed_cost_storage(tier: dict[str, float], discount_rate: float, power_gw: float) -> float:
    """Annualised capital cost ($/yr) of ``power_gw`` GW of a storage tier (energy = power × duration)."""
    energy_gwh = power_gw * float(tier.get("duration_hr", _DEFAULT_SHORT_DURATION_HR))
    return 1e6 * energy_gwh * float(tier.get("capex_usd_kwh", 0.0)) * crf(
        discount_rate, float(tier.get("lifetime_yr", _DEFAULT_STORAGE_LIFETIME_YR))
    )


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
    """Grow the checked generators and/or storage to meet 100% of load at least system cost.

    The binding constraint for "no unserved hour" is **reliability**: firm capacity must cover
    the residual net-load *peak* — the largest unserved hour, a low-renewable lull (a VRE
    drought). So every candidate is priced **per GW of that peak it actually shaves**, measured
    by re-dispatching. The numerator is the increment's *full* cost — annualised fixed cost
    **plus** the fuel + carbon it burns over the energy it serves — so the ranking is a
    screening curve:

    * a **wide** unserved block (an energy shortfall) makes the running term dominate, so
      cheap-to-run baseload (nuclear) wins even though it needs more GW;
    * a **narrow** peak (few hours) makes the fixed term dominate, so a cheap-to-build peaker
      (gas) wins.

    Dividing by GW-of-peak (not MWh-of-energy) is what keeps **storage** honest: it is built
    only to the extent it lowers the firm-capacity requirement.

    **Renewables can firm too — but only through storage.** Solar and wind are valid candidates
    when checked: overbuilding VRE raises the surplus that charges storage, which then discharges
    into the shortfall and shaves the peak. Because the metric is measured *through* the storage
    dispatch, a VRE increment is credited only for the peak it lets storage cover — so VRE alone
    (no storage, or storage already saturated) shaves ~0 and scores ∞, while VRE + enough storage
    can reach 100% at a (high) cost the tool then makes visible. Whether it fully closes depends
    on storage energy vs the longest drought: a multi-day lull needs storage deeper than any
    single-cycle can bridge, so some residual may remain and is reported.

    Returns ``(added_by_key, grown_storage_tiers, note)`` where ``added_by_key`` may include a
    ``"storage"`` entry (added short-duration power, GW).
    """
    gens = profile["generators"]
    discount = profile["discount_rate"]
    expandable_gens = [g for g in expandable if g in gens]  # VRE included: it firms via storage
    tiers = [dict(tier) for tier in storage_tiers]
    short_tier = next((tier for tier in tiers if tier.get("name") == "short"), None)
    can_storage = "storage" in expandable and short_tier is not None
    added: dict[str, float] = {g: 0.0 for g in expandable_gens}
    capacities = {key: max(0.0, float(value)) for key, value in base_capacities.items()}

    if not expandable_gens and not can_storage:
        return {}, tiers, "Select a generator or storage to expand to meet 100% load."

    def _unserved(caps: dict[str, float], trs: list[dict[str, float]]) -> tuple[float, float]:
        """Return (unserved energy TWh, residual peak GW) for a candidate build."""
        result = dispatch_hourly(
            profile=profile, year_profile=year_profile, shares=caps,
            annual_demand_twh=annual_demand_twh, carbon_price=carbon_price,
            capacities_gw=caps, storage_tiers=trs,
        )
        return float(np.sum(result.unserved_gw)) / 1000, float(np.max(result.unserved_gw))

    unserved_twh, peak_gw = _unserved(capacities, tiers)
    if unserved_twh <= _EXPANSION_UNSERVED_TOL_TWH:
        return {}, tiers, ""

    def _apply(key: str, amount: float) -> None:
        """Add ``amount`` GW to a candidate (generator capacity or short-storage power) in place."""
        if key == "storage":
            short_tier["power_gw"] = float(short_tier.get("power_gw", 0.0)) + amount  # type: ignore[union-attr]
        else:
            capacities[key] = capacities.get(key, 0.0) + amount
        added[key] = added.get(key, 0.0) + amount

    base_step = max(1.0, peak_gw / _EXPANSION_STEP_DIVISOR)
    max_step = base_step * (2.0**_EXPANSION_MAX_STEP_DOUBLINGS)
    step = base_step
    last_key: str | None = None
    last_amount = 0.0

    for _ in range(_EXPANSION_MAX_STEPS):
        if unserved_twh <= _EXPANSION_UNSERVED_TOL_TWH:
            break
        best_key: str | None = None
        best_metric = float("inf")

        # Generator candidates (VRE too): (fixed + fuel/carbon on energy served) per GW of peak shaved.
        for g in expandable_gens:
            trial = dict(capacities)
            trial[g] = trial.get(g, 0.0) + step
            trial_twh, trial_peak = _unserved(trial, tiers)
            shaved = peak_gw - trial_peak
            if shaved <= 1e-6:  # this increment does not relax the firm-capacity constraint
                continue
            served_mwh = max(unserved_twh - trial_twh, 0.0) * 1e6
            running = _marginal_cost_usd_mwh(gens[g], carbon_price) * served_mwh
            metric = (_annual_fixed_cost_gen(gens[g], discount, step) + running) / shaved
            if metric < best_metric:
                best_key, best_metric = g, metric

        # Storage candidate: one +step GW of short-duration power — only wins if it shaves peak.
        if can_storage:
            trial_tiers = [dict(t) for t in tiers]
            trial_short = next(t for t in trial_tiers if t.get("name") == "short")
            trial_short["power_gw"] = float(trial_short.get("power_gw", 0.0)) + step
            _, trial_peak = _unserved(capacities, trial_tiers)
            shaved = peak_gw - trial_peak
            if shaved > 1e-6:
                metric = _annual_fixed_cost_storage(short_tier, discount, step) / shaved
                if metric < best_metric:
                    best_key, best_metric = "storage", metric

        if best_key is None:
            # No candidate shaves the peak at this granularity. VRE + storage has a *threshold*
            # response — a small overbuild does nothing, a large one firms — so escalate the step
            # to cross it before concluding the peak cannot be closed.
            if step < max_step:
                step *= 2.0
                continue
            break

        _apply(best_key, step)
        last_key, last_amount = best_key, step
        unserved_twh, peak_gw = _unserved(capacities, tiers)
        step = max(base_step, step / 2.0)  # regain fine control after a successful commit

    # Trim overshoot: the move that finally closed the gap is often a large escalated VRE step.
    # Bisect it down to the smallest amount that still holds 100% load.
    if last_key is not None and unserved_twh <= _EXPANSION_UNSERVED_TOL_TWH and last_amount > base_step:
        keep_lo, keep_hi = 0.0, last_amount  # how much of the last move we can give back
        for _ in range(_EXPANSION_TRIM_ITERS):
            give_back = (keep_lo + keep_hi) / 2.0
            _apply(last_key, -give_back)
            twh, _ = _unserved(capacities, tiers)
            _apply(last_key, give_back)
            if twh <= _EXPANSION_UNSERVED_TOL_TWH:
                keep_lo = give_back  # still feasible after removing this much
            else:
                keep_hi = give_back
        if keep_lo > 0.0:
            _apply(last_key, -keep_lo)
            unserved_twh, peak_gw = _unserved(capacities, tiers)

    added = {key: value for key, value in added.items() if value > 1e-6}
    only_vre_expandable = bool(expandable_gens) and all(g in _DISPATCH_VRE for g in expandable_gens)
    note = ""
    if unserved_twh > _EXPANSION_UNSERVED_TOL_TWH:
        if only_vre_expandable and not can_storage:
            note = (
                "Renewables cannot firm the load on their own — also make storage expandable "
                "(or add a firm generator) so surplus can be shifted into the shortfall."
            )
        elif only_vre_expandable:
            note = (
                "Renewables + this storage narrowed the gap but a multi-day lull remains — the "
                "storage cannot hold enough energy to bridge it. Add more/longer-duration storage "
                "or a firm generator to reach 100%."
            )
        else:
            note = (
                "Could not fully close the gap with the selected options — add another "
                "dispatchable generator or more storage."
            )
    elif can_storage and "storage" not in added and not only_vre_expandable and expandable_gens:
        note = (
            "Storage was not built: short-duration storage cannot cover the binding peak "
            "(a low-renewable lull), so firm generation is the cheaper way to reach 100%."
        )
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
