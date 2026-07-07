from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

from backend.core.adequacy import estimate_adequacy
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

    Returns ``(added_by_key, grown_storage_tiers, note)`` where ``added_by_key`` may include
    ``"storage"`` (added short-duration power, GW) and/or ``"storage_long"`` (added
    long-duration power, GW) alongside the grown generators.
    """
    gens = profile["generators"]
    discount = profile["discount_rate"]
    expandable_gens = [g for g in expandable if g in gens]  # VRE included: it firms via storage
    tiers = [dict(tier) for tier in storage_tiers]
    # Both storage tiers are expansion candidates when "storage" is checked: short-duration
    # firms sharp/diurnal peaks, long-duration bridges multi-day droughts. Keyed for reporting.
    storage_candidates: dict[str, dict[str, float]] = {}
    if "storage" in expandable:
        for key, name in (("storage", "short"), ("storage_long", "long")):
            tier = next((t for t in tiers if t.get("name") == name), None)
            if tier is not None:
                storage_candidates[key] = tier
    can_storage = bool(storage_candidates)
    added: dict[str, float] = {g: 0.0 for g in expandable_gens}
    capacities = {key: max(0.0, float(value)) for key, value in base_capacities.items()}

    if not expandable_gens and not can_storage:
        return {}, tiers, "Select a generator or storage to expand to meet 100% load."

    def _unserved(caps: dict[str, float], trs: list[dict[str, float]]) -> tuple[float, float]:
        """Return (unserved energy TWh, residual peak GW) for a candidate build.

        Sizing measures a device's *firm* contribution, so storage is dispatched reliability-only
        here (``economic_storage=False``) — its charge is held for the peak, not spent on arbitrage.
        """
        result = dispatch_hourly(
            profile=profile, year_profile=year_profile, shares=caps,
            annual_demand_twh=annual_demand_twh, carbon_price=carbon_price,
            capacities_gw=caps, storage_tiers=trs, economic_storage=False,
        )
        return float(np.sum(result.unserved_gw)) / 1000, float(np.max(result.unserved_gw))

    unserved_twh, peak_gw = _unserved(capacities, tiers)
    if unserved_twh <= _EXPANSION_UNSERVED_TOL_TWH:
        return {}, tiers, ""

    def _apply(key: str, amount: float) -> None:
        """Add ``amount`` GW to a candidate (generator capacity or a storage tier's power) in place."""
        if key in storage_candidates:
            tier = storage_candidates[key]
            tier["power_gw"] = float(tier.get("power_gw", 0.0)) + amount
        else:
            capacities[key] = capacities.get(key, 0.0) + amount
        added[key] = added.get(key, 0.0) + amount

    base_step = max(1.0, peak_gw / _EXPANSION_STEP_DIVISOR)
    max_step = base_step * (2.0**_EXPANSION_MAX_STEP_DOUBLINGS)
    last_key: str | None = None
    last_amount = 0.0

    def _best_size(
        probe: "Any", cost_at: "Any"
    ) -> tuple[float | None, float]:
        """Cheapest (size, $/GW-of-peak) for one candidate, scanning escalating increments.

        A candidate's marginal peak-shaving is not monotonic in a single fixed step: a small
        VRE overbuild shaves nothing until it crosses a threshold, and a fixed-duration battery
        saturates its energy window so a small power bump shaves ~0 while a larger one firms the
        peak. Scanning ``base_step`` upward (doubling) and taking the *minimum* $/GW-shaved lets
        each candidate be priced at the size where it is actually effective — so cheap short
        storage is not abandoned for the dear long tier just because one small step saturated.
        """
        best_sz: float | None = None
        best_m = float("inf")
        worse = 0
        size = base_step
        while size <= max_step:
            trial_twh, trial_peak = probe(size)
            shaved = peak_gw - trial_peak
            if shaved > 1e-6:
                metric = cost_at(size, max(unserved_twh - trial_twh, 0.0)) / shaved
                if metric < best_m:
                    best_m, best_sz, worse = metric, size, 0
                else:
                    worse += 1
                    if worse >= 2:  # past this candidate's sweet spot
                        break
                if shaved >= 0.999 * peak_gw:  # fully shaves the peak; larger is pointless
                    break
            size *= 2.0
        return best_sz, best_m

    for _ in range(_EXPANSION_MAX_STEPS):
        if unserved_twh <= _EXPANSION_UNSERVED_TOL_TWH:
            break
        best_key: str | None = None
        best_size = 0.0
        best_metric = float("inf")

        # Firm dispatchables shave the peak monotonically (1 GW firm ≈ 1 GW less peak), so a single
        # fine step is enough and keeps a baseload+peaker blend sharp: (fixed + fuel/carbon on the
        # energy served) per GW of peak shaved.
        for g in expandable_gens:
            if g in _DISPATCH_VRE:
                continue
            trial = dict(capacities)
            trial[g] = trial.get(g, 0.0) + base_step
            trial_twh, trial_peak = _unserved(trial, tiers)
            shaved = peak_gw - trial_peak
            if shaved <= 1e-6:
                continue
            served_mwh = max(unserved_twh - trial_twh, 0.0) * 1e6
            running = _marginal_cost_usd_mwh(gens[g], carbon_price) * served_mwh
            metric = (_annual_fixed_cost_gen(gens[g], discount, base_step) + running) / shaved
            if metric < best_metric:
                best_key, best_size, best_metric = g, base_step, metric

        # VRE (threshold response) and storage (fixed-duration saturation) are non-monotonic in a
        # single step, so each is priced at the escalated size where it is actually effective.
        for g in expandable_gens:
            if g not in _DISPATCH_VRE:
                continue

            def probe(size: float, g: str = g) -> tuple[float, float]:
                trial = dict(capacities)
                trial[g] = trial.get(g, 0.0) + size
                return _unserved(trial, tiers)

            def cost_at(size: float, served_twh: float, g: str = g) -> float:
                running = _marginal_cost_usd_mwh(gens[g], carbon_price) * served_twh * 1e6
                return _annual_fixed_cost_gen(gens[g], discount, size) + running

            size, metric = _best_size(probe, cost_at)
            if size is not None and metric < best_metric:
                best_key, best_size, best_metric = g, size, metric

        # Short firms diurnal peaks cheaply; long-duration is dear per GW but is the only thing that
        # can bridge a multi-day drought. Priced at its efficient size, so long wins only where short
        # genuinely cannot shave the peak more cheaply.
        for skey, stier in storage_candidates.items():
            tier_name = stier.get("name")

            def probe(size: float, tier_name: "Any" = tier_name) -> tuple[float, float]:
                trial_tiers = [dict(t) for t in tiers]
                trial_tier = next(t for t in trial_tiers if t.get("name") == tier_name)
                trial_tier["power_gw"] = float(trial_tier.get("power_gw", 0.0)) + size
                return _unserved(capacities, trial_tiers)

            def cost_at(size: float, served_twh: float, stier: dict[str, float] = stier) -> float:
                return _annual_fixed_cost_storage(stier, discount, size)

            size, metric = _best_size(probe, cost_at)
            if size is not None and metric < best_metric:
                best_key, best_size, best_metric = skey, size, metric

        if best_key is None:  # nothing shaves the peak, even escalated to max_step
            break

        _apply(best_key, best_size)
        last_key, last_amount = best_key, best_size
        unserved_twh, peak_gw = _unserved(capacities, tiers)

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
    elif (
        can_storage
        and not any(key in added for key in storage_candidates)
        and not only_vre_expandable
        and expandable_gens
    ):
        note = (
            "Storage was not built: it could not cover the binding peak (a low-renewable lull) "
            "more cheaply than firm generation, which reaches 100% at lower cost here."
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
            block_days=int(ensemble.get("block_days", 14)),
        )
    return EnsembleSettings(
        method=getattr(ensemble, "method", "jitter"),
        n_samples=int(getattr(ensemble, "n_samples", 5)),
        sigma=float(getattr(ensemble, "sigma", 0.04)),
        seed=int(getattr(ensemble, "seed", 42)),
        block_days=int(getattr(ensemble, "block_days", 14)),
    )


def _median_metric(summary: dict[str, Any], group: str, key: str) -> float:
    return float(summary["metrics"][group].get(key, {}).get("median", 0.0))


def _median_scalar(summary: dict[str, Any], key: str) -> float:
    return float(summary["metrics"]["scalars"].get(key, {}).get("median", 0.0))


# Generators eligible for the clean-energy subsidy (ITC / PTC) — the low-carbon set.
_CLEAN_GENERATORS = {"solar", "wind_onshore", "nuclear"}

# Generators that burn imported fuel — bear the fuel-import tariff and count toward the
# energy-security import-dependency metric. Nuclear fuel is excluded (small, largely stockpiled).
_IMPORTED_FUEL_GENERATORS = {"gas_ccgt", "coal", "other"}


def _apply_fuel_import_tariff(profile: dict[str, Any], tariff_fraction: float) -> None:
    """Raise imported-fuel generators' fuel cost by ``tariff_fraction`` in place.

    A fuel-import tariff (energy-security lever) surcharges the delivered price of imported fuel.
    Scaling ``fuel_usd_mmbtu`` on the imported-fuel set flows straight through both the dispatch
    merit order (via short-run marginal cost, so a high tariff can reorder the stack) and the LCOE
    fuel component — no separate plumbing needed. Applied to a deep-merged profile copy, so the
    on-disk profile is untouched.
    """
    tariff = max(0.0, float(tariff_fraction))
    if tariff <= 0.0:
        return
    for name, cfg in profile["generators"].items():
        if name in _IMPORTED_FUEL_GENERATORS and float(cfg.get("fuel_usd_mmbtu", 0.0)) > 0.0:
            cfg["fuel_usd_mmbtu"] = float(cfg["fuel_usd_mmbtu"]) * (1.0 + tariff)


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
    fuel_import_tariff_pct: float | None = None,
) -> dict[str, Any]:
    base_profile = load_country_profile(country)
    profile = deep_merge(base_profile, custom_params or {})
    if annual_demand_twh is not None:
        profile["annual_generation_twh"] = annual_demand_twh
    if fuel_import_tariff_pct:
        _apply_fuel_import_tariff(profile, fuel_import_tariff_pct)

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

    # Resource adequacy: LOLE / LOLP / EUE and their distribution across the ensemble's
    # jointly-sampled weather scenarios. Only meaningful with a real spread — most so under the
    # block-bootstrap sampler, which preserves the multi-day droughts that dominate the tail.
    member_unserved = dispatch_summary.get("member_unserved_gw", [])
    adequacy = (
        estimate_adequacy(member_unserved, profile["annual_generation_twh"])
        if member_unserved
        else None
    )
    if adequacy is not None:
        adequacy["ensemble_method"] = settings.method

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

    # Energy-security metric: the share of generation met by imported fuel (gas/coal/other).
    # A higher share means more exposure to fuel-price and supply shocks — the lever the
    # fuel-import tariff pushes against by pricing that exposure into the merit order.
    current["import_dependency"] = sum(
        _median_metric(dispatch_summary, "realized_share", gen) for gen in _IMPORTED_FUEL_GENERATORS
    )

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
    if adequacy is not None:
        result["adequacy"] = adequacy
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
    fuel_import_tariff_pct: float | None = None,
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
        fuel_import_tariff_pct=fuel_import_tariff_pct,
    )


def _interpolate(start: float, end: float, fraction: float) -> float:
    """Linear blend ``start → end`` at ``fraction ∈ [0, 1]``."""
    return start * (1.0 - fraction) + end * fraction


def simulate_pathway(
    country: str,
    start_capacities: dict[str, float],
    target_capacities: dict[str, float],
    years: list[int],
    carbon_price_start: float = 0.0,
    carbon_price_end: float = 0.0,
    annual_demand_twh_start: float | None = None,
    annual_demand_twh_end: float | None = None,
    ensemble: Any | None = None,
    **calculate_kwargs: Any,
) -> dict[str, Any]:
    """Run the single-year model along a planning pathway and return the trajectory.

    Each milestone year is a snapshot: generator capacities are **linearly interpolated** from
    today's fleet (``start_capacities``) to the end-of-horizon fleet (``target_capacities``) — so a
    target of 0 phases a generator out and a higher target builds it — the carbon price ramps from
    ``carbon_price_start`` to ``carbon_price_end``, and demand (optionally) grows from start to end.
    Each snapshot is evaluated with the full hourly-dispatch model, so the pathway captures how
    system LCOE, emissions, and import dependency evolve as the mix, carbon price, and demand shift.

    Args:
        country: Country code.
        start_capacities: Installed capacity by generator today (GW).
        target_capacities: Installed capacity by generator at the final year (GW).
        years: Ascending milestone years, e.g. ``[2025, 2030, 2040, 2050]``.
        carbon_price_start / carbon_price_end: Carbon price ($/tCO₂) at the first / last year.
        annual_demand_twh_start / _end: Optional demand (TWh) at the first / last year; if either is
            omitted both fall back to ``calculate_kwargs['annual_demand_twh']`` (flat demand).
        ensemble: Ensemble settings passed to each snapshot.
        **calculate_kwargs: Forwarded verbatim to :func:`calculate_system_lcoe` (carbon price,
            demand, capacities, and ensemble are supplied per-year and must not be duplicated here).

    Returns:
        ``{"country", "years", "steps": [{year, fraction, carbon_price, annual_demand_twh,
        system_lcoe, annual_emissions_mtco2, emission_intensity, import_dependency,
        capacities_gw}, ...]}`` — one step per milestone year.
    """
    if not years:
        raise ValueError("A pathway needs at least one milestone year.")
    keys = sorted(set(start_capacities) | set(target_capacities))
    first_year, last_year = years[0], years[-1]
    span = float(last_year - first_year) or 1.0
    flat_demand = calculate_kwargs.pop("annual_demand_twh", None)
    demand_start = annual_demand_twh_start if annual_demand_twh_start is not None else flat_demand
    demand_end = annual_demand_twh_end if annual_demand_twh_end is not None else flat_demand

    steps: list[dict[str, Any]] = []
    for year in years:
        fraction = (year - first_year) / span
        capacities = {
            key: _interpolate(float(start_capacities.get(key, 0.0)), float(target_capacities.get(key, 0.0)), fraction)
            for key in keys
        }
        carbon_price = _interpolate(carbon_price_start, carbon_price_end, fraction)
        demand = (
            _interpolate(float(demand_start), float(demand_end), fraction)
            if demand_start is not None and demand_end is not None
            else None
        )
        result = calculate_system_lcoe(
            country=country,
            shares=capacities,  # ignored when capacities_gw is set, but the signature requires it
            capacities_gw=capacities,
            carbon_price=carbon_price,
            annual_demand_twh=demand,
            ensemble=ensemble,
            **calculate_kwargs,
        )
        steps.append({
            "year": year,
            "fraction": round(fraction, 4),
            "carbon_price": round(carbon_price, 2),
            "annual_demand_twh": result["annual_demand_twh"],
            "system_lcoe": result["system_lcoe"],
            "annual_emissions_mtco2": result["annual_emissions_mtco2"],
            "emission_intensity": result["emission_intensity"],
            "import_dependency": result["import_dependency"],
            "capacities_gw": {key: round(value, 3) for key, value in capacities.items()},
        })
    return {"country": country.upper(), "years": list(years), "steps": steps}


# ── Size-to-adequacy (grow a firm resource until LOLE ≤ target) ──────────────────
_SIZE_ADEQUACY_MAX_DOUBLINGS: int = 10  # upper-bound search before giving up
_SIZE_ADEQUACY_BISECT_ITERS: int = 12   # bisection refinement of the minimal firm capacity


def size_for_adequacy(
    country: str,
    capacities: dict[str, float],
    firm_key: str,
    lole_target_hours: float,
    carbon_price: float = 0.0,
    annual_demand_twh: float | None = None,
    ensemble: Any | None = None,
    ess_short_power_gw: float | None = None,
    ess_short_duration_hr: float | None = None,
    ess_long_power_gw: float | None = None,
    ess_long_duration_hr: float | None = None,
    max_gw: float | None = None,
) -> dict[str, Any]:
    """Least firm capacity of ``firm_key`` that holds resource adequacy to ``lole_target_hours``.

    The probabilistic analogue of "meet 100% load": rather than zero unserved on the single worst
    weather sample, grow one firm resource until the **ensemble** LOLE (loss-of-load expectation,
    h/yr) drops to a reliability standard (e.g. 2.4 h/yr ≈ "1 day in 10 years"). LOLE is monotone
    decreasing in firm capacity and, with a fixed ensemble seed, deterministic — the same weather
    scenarios are dispatched at every trial capacity — so the minimal capacity is found by an
    upper-bound search followed by bisection. Use a block-bootstrap ensemble so the LOLE reflects
    the multi-day droughts that set the standard.

    Args:
        country: Country code.
        capacities: Installed capacity by generator (GW); ``firm_key`` is the one grown.
        firm_key: Generator whose capacity is sized (a dispatchable, e.g. ``"gas_ccgt"``).
        lole_target_hours: Target LOLE (h/yr) — the reliability standard to meet.
        carbon_price, annual_demand_twh, ensemble, ess_*: Passed to each adequacy evaluation.
        max_gw: Optional ceiling on ``firm_key`` capacity; the search stops there.

    Returns:
        Dict with ``firm_key``, ``required_gw`` (sized capacity), ``added_gw`` (vs the start),
        ``baseline_lole_hours``, ``lole_hours`` (achieved), ``lole_target_hours``, ``met``,
        ``system_lcoe`` and ``annual_system_cost_usd_billion`` at the sized point.
    """
    base = {key: max(0.0, float(value)) for key, value in capacities.items()}
    base.setdefault(firm_key, 0.0)
    start = base[firm_key]
    target = max(0.0, float(lole_target_hours))
    cache: dict[float, tuple[float, dict[str, Any]]] = {}

    def evaluate(gw: float) -> tuple[float, dict[str, Any]]:
        key = round(gw, 4)
        if key not in cache:
            caps = dict(base)
            caps[firm_key] = gw
            result = calculate_system_lcoe(
                country=country, shares=caps, capacities_gw=caps, carbon_price=carbon_price,
                annual_demand_twh=annual_demand_twh, ensemble=ensemble,
                ess_short_power_gw=ess_short_power_gw, ess_short_duration_hr=ess_short_duration_hr,
                ess_long_power_gw=ess_long_power_gw, ess_long_duration_hr=ess_long_duration_hr,
            )
            adequacy = result.get("adequacy")
            lole = float(adequacy["lole_hours"]) if adequacy else 0.0
            cache[key] = (lole, result)
        return cache[key]

    baseline_lole, baseline_result = evaluate(start)

    def _report(gw: float, met: bool) -> dict[str, Any]:
        lole, result = evaluate(gw)
        return {
            "firm_key": firm_key,
            "required_gw": round(gw, 2),
            "added_gw": round(gw - start, 2),
            "baseline_lole_hours": round(baseline_lole, 2),
            "lole_hours": round(lole, 2),
            "lole_target_hours": target,
            "met": met,
            "system_lcoe": result["system_lcoe"],
            "annual_system_cost_usd_billion": result["annual_system_cost_usd_billion"],
        }

    if baseline_lole <= target:
        return _report(start, met=True)  # already adequate — no build needed

    # Upper-bound search: double firm capacity until the target is met (or the ceiling is hit).
    ceiling = max_gw if max_gw is not None else max(start, 1.0) * (2.0**_SIZE_ADEQUACY_MAX_DOUBLINGS)
    hi = max(start, 1.0)
    for _ in range(_SIZE_ADEQUACY_MAX_DOUBLINGS):
        if evaluate(min(hi, ceiling))[0] <= target or hi >= ceiling:
            break
        hi = min(hi * 2.0, ceiling)
    if evaluate(min(hi, ceiling))[0] > target:
        return _report(min(hi, ceiling), met=False)  # standard unreachable within the ceiling

    # Bisect [start, hi] for the minimal firm capacity that still meets the target.
    lo, hi = start, min(hi, ceiling)
    for _ in range(_SIZE_ADEQUACY_BISECT_ITERS):
        mid = (lo + hi) / 2.0
        if evaluate(mid)[0] <= target:
            hi = mid
        else:
            lo = mid
    return _report(hi, met=True)
