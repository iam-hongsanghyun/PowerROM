from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

from backend.core.adequacy import estimate_adequacy
from backend.core.radar import build_radar
from backend.core.dispatch_engine import (
    VRE_GENERATORS as _DISPATCH_VRE,
    dispatch_hourly,
    run_dispatch_ensemble,
)
from backend.core.function_catalog import evaluate_function
from backend.core.hourly_profiles import EnsembleSettings, load_hourly_profiles, sample_ensemble

PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "country_profiles"
VRE_GENERATORS = {"solar", "wind_onshore", "wind_offshore"}

# ── Model constants ────────────────────────────────────────────────────────────
# These are the only numeric literals that are NOT read from country profiles.

# Round-trip efficiency of each storage tier (energy delivered ÷ energy stored),
# used when the profile does not specify `round_trip_efficiency`.
_SHORT_STORAGE_RTE: float = 0.85  # intraday lithium battery
_PHS_STORAGE_RTE: float = 0.78    # pumped hydro (IHA / IEA typical round-trip)
_LONG_STORAGE_RTE: float = 0.45   # seasonal store (e.g. hydrogen)

# Fallback storage duration (hours) when neither the request nor the profile sets it.
_DEFAULT_SHORT_DURATION_HR: float = 4.0
_DEFAULT_PHS_DURATION_HR: float = 10.0    # bulk pumped hydro: hours-to-a-day of shifting
_DEFAULT_LONG_DURATION_HR: float = 168.0

# Fallback throughput bounds when the profile ess block omits them: depth-of-discharge and annual
# full-cycle count (intraday batteries cycle ~daily; pumped hydro somewhat less; seasonal stores
# only a few times a year).
_DEFAULT_DOD: float = 0.9
_DEFAULT_SHORT_CYCLES: float = 300.0
_DEFAULT_PHS_CYCLES: float = 200.0
_DEFAULT_LONG_CYCLES: float = 30.0

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
    ess_phs_power_gw: float | None = None,
    ess_phs_duration_hr: float | None = None,
) -> list[dict[str, float]]:
    """Assemble the user-set storage tiers for endogenous dispatch and costing.

    Three tiers: ``short`` (intraday battery), ``phs`` (pumped hydro — bulk, cheap per kWh,
    ~78% round-trip), and ``long`` (seasonal store). Power (GW) and duration (h) are user
    inputs (energy = power × duration); capex, lifetime, and round-trip efficiency come from
    the profile ``ess`` block (with fallbacks). Power defaults to 0 (no storage) when unset.
    """
    ess = profile.get("ess", {})
    specs = (
        ("short", ess.get("short_dur", {}), ess_short_power_gw, ess_short_duration_hr,
         _DEFAULT_SHORT_DURATION_HR, _SHORT_STORAGE_RTE, _DEFAULT_SHORT_CYCLES),
        ("phs", ess.get("phs_dur", {}), ess_phs_power_gw, ess_phs_duration_hr,
         _DEFAULT_PHS_DURATION_HR, _PHS_STORAGE_RTE, _DEFAULT_PHS_CYCLES),
        ("long", ess.get("long_dur", {}), ess_long_power_gw, ess_long_duration_hr,
         _DEFAULT_LONG_DURATION_HR, _LONG_STORAGE_RTE, _DEFAULT_LONG_CYCLES),
    )
    tiers: list[dict[str, float]] = []
    for name, cfg, power, duration, default_duration, default_rte, default_cycles in specs:
        tier = {
            "name": name,
            "power_gw": float(power) if power is not None else 0.0,
            "duration_hr": float(duration) if duration is not None else float(cfg.get("duration_hr", default_duration)),
            "efficiency": float(cfg.get("round_trip_efficiency", default_rte)),
            "capex_usd_kwh": float(cfg.get("capex_usd_kwh", 0.0)),
            "lifetime_yr": float(cfg.get("lifetime_yr", 15.0)),
            # Depth-of-discharge and annual full-cycle count bound the LDC active-charge throughput.
            "dod": float(cfg.get("dod", _DEFAULT_DOD)),
            "cycles_per_year": float(cfg.get("cycles_per_year", default_cycles)),
        }
        # Short tier's economic-arbitrage price-percentile window (config; dispatch falls back to its
        # own default when absent). Only meaningful for the short/intraday tier.
        if "arbitrage_price_percentile" in cfg:
            tier["arbitrage_price_percentile"] = float(cfg["arbitrage_price_percentile"])
        tiers.append(tier)
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
    for name in ("short", "phs", "long"):
        result.setdefault(f"ess_{name}_gwh", 0.0)
        result.setdefault(f"ess_{name}_gw", 0.0)
        result.setdefault(f"ess_{name}_lcoe", 0.0)
    return result


# ── Capacity expansion (least firm-cost, to meet 100% load) ─────────────────────
_EXPANSION_UNSERVED_TOL_TWH: float = 0.02   # treat as "no unserved hour"
_EXPANSION_MAX_STEPS: int = 90              # firming increments before giving up (a plateau backstop)
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
    # Offshore shares the wind weather shape, so count both wind fleets against the net-load peak.
    wind_cap = capacities.get("wind_onshore", 0.0) + capacities.get("wind_offshore", 0.0)

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
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
    ramp_up: dict[str, float] | None = None,
    ramp_down: dict[str, float] | None = None,
) -> tuple[dict[str, float], list[dict[str, float]], str]:
    """Grow the fleet to meet 100% of load at least system cost — a GUARANTEED hard threshold.

    "Meet 100% load" must reach zero unserved; "cannot" is not an outcome. The binding constraint
    for "no unserved hour" is **reliability**: firm capacity must cover the residual net-load
    *peak* — the largest unserved hour, a low-renewable lull (a VRE drought). Each step of a
    verified loop re-dispatches and compares the cheapest way to shave the current peak:

    * a **firm generator** — annualised fixed cost of ``peak / ceiling`` GW (the "1 MW vs capex"
      ratio). Dispatchable, needs no charging, and can always cover the peak by adding capacity;
    * **shifting the load with storage** — annualised capex of a reservoir sized to the deepest
      supply−demand draw-down (Rippl mass-curve), at power = the peak.

    It grows the cheaper. Storage wins for cheap **diurnal** cycling; a **multi-day** lull makes
    the reservoir dearer than firm capacity, so a generator takes over instead of building an
    absurd seasonal store (no more "~120 TWh / 4800 h" reservoirs). **Renewables** are grown first
    to the annual energy balance so surplus can charge storage; they firm only *through* storage.

    Because a dispatchable generator can always cover the peak, the **cheapest fleet thermal is
    always available as a backstop even if unchecked**, so closure is guaranteed. The only exit
    with residual is a true physical plateau — no dispatchable plant exists and storage cannot
    charge — where adding capacity genuinely adds no generation; that (only) case is reported.

    Returns ``(added_by_key, grown_storage_tiers, note)`` where ``added_by_key`` may include
    ``"storage"`` (added short/battery power, GW), ``"storage_phs"`` (pumped-hydro power, GW),
    ``"storage_long"`` (seasonal power, GW), and/or a firm generator grown to firm the peak.
    """
    gens = profile["generators"]
    discount = profile["discount_rate"]
    expandable = list(expandable or [])  # tolerate None (the schema default when nothing is checked)
    expandable_gens = [g for g in expandable if g in gens]  # VRE included: it firms via storage
    tiers = [dict(tier) for tier in storage_tiers]
    # Storage is expandable per tier so the user picks which type to build: "storage_short" (battery),
    # "storage_phs" (pumped hydro), "storage_long" (seasonal). "storage" is a legacy alias enabling
    # short + long. Reporting keys: short -> "storage", phs -> "storage_phs", long -> "storage_long".
    tier_by_name = {t.get("name"): t for t in tiers}
    _REPORT_KEY = {"short": "storage", "phs": "storage_phs", "long": "storage_long"}
    storage_candidates: dict[str, dict[str, float]] = {}
    checked_names: set[str] = set()
    if "storage" in expandable:  # legacy alias
        checked_names.update({"short", "long"})
    for key, name in (("storage_short", "short"), ("storage_phs", "phs"), ("storage_long", "long")):
        if key in expandable:
            checked_names.add(name)
    for name in checked_names:
        tier = tier_by_name.get(name)
        if tier is not None:
            storage_candidates[_REPORT_KEY[name]] = tier
    added: dict[str, float] = {g: 0.0 for g in expandable_gens}
    capacities = {key: max(0.0, float(value)) for key, value in base_capacities.items()}

    # "Meet 100% load" is a HARD promise — there is no "cannot expand" outcome. Even with nothing
    # (or only VRE / only storage) checked, the guaranteed firm closer below can still cover the peak.

    def _unserved(caps: dict[str, float], trs: list[dict[str, float]]) -> tuple[float, float]:
        """Return (unserved energy TWh, residual peak GW) for a candidate build.

        Sizing measures a device's *firm* contribution, so storage is dispatched reliability-only
        here (``economic_storage=False``) — its charge is held for the peak, not spent on arbitrage.
        """
        result = dispatch_hourly(
            profile=profile, year_profile=year_profile, shares=caps,
            annual_demand_twh=annual_demand_twh, carbon_price=carbon_price,
            capacities_gw=caps, storage_tiers=trs, economic_storage=False,
            min_cf=min_cf, max_cf=max_cf, ramp_up=ramp_up, ramp_down=ramp_down,
        )
        return float(np.sum(result.unserved_gw)) / 1000, float(np.max(result.unserved_gw))

    unserved_twh, _peak0 = _unserved(capacities, tiers)
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

    # ── LDC + reservoir sizing (meet 100% load is a HARD threshold) ─────────────────
    # "Meet 100% load" must actually reach zero unserved. The old greedy solver treated VRE as a
    # firming tool and spiralled to absurd builds; the fix is a two-part deterministic sizing:
    #   1. grow the checked VRE until the ANNUAL energy balances (supply ≥ demand + round-trip loss),
    #   2. firm the residual — a checked firm generator rides the whole drought, OR, if storage is the
    #      only firming option, size it to the RESERVOIR: power = worst hourly deficit, energy = worst
    #      cumulative draw-down of the supply−demand signal (so the store never empties).
    # This is the classic energy-balance + reservoir screening; it hits zero unserved by construction.
    vre_expandable = [g for g in expandable_gens if g in _DISPATCH_VRE]
    firm_expandable = [g for g in expandable_gens if g not in _DISPATCH_VRE]
    hours = len(year_profile.demand_norm)
    avg_load = annual_demand_twh * 1000.0 / hours
    demand = year_profile.demand_norm * avg_load
    _RESERVOIR_RTE = 0.80        # blended storage round-trip used for the energy margin + drawdown
    _ENERGY_MARGIN = 0.12        # build VRE ~12% above demand so there is surplus to refill storage

    def _hourly_unserved(caps: dict[str, float], trs: list[dict[str, float]]) -> np.ndarray:
        return dispatch_hourly(
            profile=profile, year_profile=year_profile, shares=caps,
            annual_demand_twh=annual_demand_twh, carbon_price=carbon_price,
            capacities_gw=caps, storage_tiers=trs, economic_storage=True,
            min_cf=min_cf, max_cf=max_cf, ramp_up=ramp_up, ramp_down=ramp_down,
        ).unserved_gw

    def _vre_output(caps: dict[str, float]) -> np.ndarray:
        out = caps.get("solar", 0.0) * year_profile.solar_cf + caps.get("wind_onshore", 0.0) * year_profile.wind_cf
        if caps.get("wind_offshore", 0.0) > 0.0:  # offshore rides the wind shape at its own mean CF
            base = float(gens.get("wind_offshore", {}).get("cf_base", 0.42))
            mean = max(float(np.mean(year_profile.wind_cf)), 1e-6)
            out = out + caps["wind_offshore"] * np.clip(year_profile.wind_cf * (base / mean), 0.0, 1.0)
        return out

    def _firm_available(caps: dict[str, float]) -> np.ndarray:
        """Max energy the firm (non-VRE) fleet can deliver per hour, run up to its ceiling."""
        total = 0.0
        for gen, block in gens.items():
            if gen in _DISPATCH_VRE or caps.get(gen, 0.0) <= 0.0:
                continue
            ceiling = float((max_cf or {}).get(gen, block.get("max_cf", block.get("cf_base", 1.0))) or 1.0)
            total = total + caps[gen] * ceiling
        return np.full(hours, total, dtype=float) if np.isscalar(total) else total

    # 1. Energy fill: grow the checked VRE so annual supply ≥ demand × (1 + margin) — enough surplus
    #    for storage to refill between droughts. Split the gap across the checked VRE by their mean CF.
    if vre_expandable:
        target = float(np.sum(demand)) * (1.0 + _ENERGY_MARGIN)
        gap = target - float(np.sum(_firm_available(capacities))) - float(np.sum(_vre_output(capacities)))
        if gap > 0.0:
            per_gwh = gap / len(vre_expandable)
            for g in vre_expandable:
                cf = max(float(np.mean(year_profile.solar_cf if g == "solar" else year_profile.wind_cf)), 0.03)
                if g == "wind_offshore":
                    cf = min(cf * 1.4, 0.6)
                _apply(g, per_gwh / (cf * hours))

    # Guaranteed firm closer: the cheapest DISPATCHABLE thermal in the fleet, priced per GW of the
    # peak it shaves — annualised fixed cost ÷ its availability ceiling (the "1 MW vs capex" ratio).
    # "Meet 100% load" is a HARD promise, so this generator is always available to firm the residual
    # peak even if the user did not check it: a power system serves its peak with firm capacity;
    # storage only shifts energy in time and cannot, alone, invent it across a multi-day lull.
    def _peak_ceiling(g: str) -> float:
        return float((max_cf or {}).get(g, gens[g].get("max_cf", gens[g].get("cf_base", 1.0))) or 1.0)

    # A generator is a firm backstop if it is dispatchable (non-VRE) and buildable (real capex).
    # NOT keyed on the optional max_cf field — 13 country profiles omit it, and _peak_ceiling already
    # falls back to cf_base, so requiring max_cf would leave those fleets with no closer (false plateau).
    firm_pool = [g for g in gens if g not in _DISPATCH_VRE and float(gens[g].get("capex_usd_kw", 0.0)) > 0.0]

    def _firm_cost_per_peak_gw(g: str) -> float:
        return _annual_fixed_cost_gen(gens[g], discount, 1.0) / max(_peak_ceiling(g), 0.05)

    firm_closer = min(firm_pool, key=_firm_cost_per_peak_gw) if firm_pool else None
    # Prefer a firm generator the user checked; otherwise fall back to the guaranteed closer.
    firm_choice = min(firm_expandable, key=_firm_cost_per_peak_gw) if firm_expandable else firm_closer
    checked_firm = set(firm_expandable)
    sqrt_eta = _RESERVOIR_RTE**0.5

    # 2. Verified least-cost closure loop — meet 100% is a HARD threshold that MUST reach zero
    #    unserved. Each step compares the cheapest way to shave the CURRENT residual peak:
    #      • a firm generator: annualised fixed cost of peak / ceiling GW, versus
    #      • shifting the load with storage: annualised capex of a reservoir sized to the deepest
    #        supply−demand draw-down (Rippl mass-curve), at power = the peak.
    #    It grows the cheaper. Storage wins for cheap diurnal cycling; a multi-day lull makes the
    #    reservoir dearer than firm capacity, so a generator takes over instead of building an absurd
    #    seasonal store. Because a dispatchable generator can always cover the peak, closure is
    #    GUARANTEED — the only exit with residual is a true plateau (no dispatchable plant exists and
    #    storage cannot charge), i.e. where adding capacity genuinely adds no generation.
    auto_firm_gw = 0.0       # firm capacity added to an UN-checked generator (worth flagging)
    prev = float("inf")
    firm_forced = False      # once storage proves it cannot help, close with firm only
    for _ in range(_EXPANSION_MAX_STEPS):
        u = _hourly_unserved(capacities, tiers)
        total_gwh = float(np.sum(u))
        if total_gwh / 1000.0 <= _EXPANSION_UNSERVED_TOL_TWH:
            break
        peak = float(np.max(u))

        # Firm option: annualised fixed cost of the capacity needed to shave the peak. Size against
        # the generator's REAL availability ceiling (only guarded against divide-by-zero, not floored
        # at 0.05) — a firm plant capped at, say, 3% CF needs peak/0.03 GW to cover the peak, and
        # under-sizing it to peak/0.05 would leave a residual and a false plateau.
        cost_firm = float("inf")
        firm_gw = 0.0
        if firm_choice is not None:
            firm_gw = max(peak / max(_peak_ceiling(firm_choice), 1e-3), 0.5)
            cost_firm = _annual_fixed_cost_gen(gens[firm_choice], discount, firm_gw)

        # Storage option: only if a tier is checked, it has a real capex (else it looks free), and
        # there is surplus for it to charge (VRE is grown, or the fleet already spills some hours).
        cost_stor = float("inf")
        reservoir = 0.0
        tier_key: str | None = None
        net = _vre_output(capacities) + _firm_available(capacities) - demand
        chargeable = bool(vre_expandable) or bool(np.any(net > 0.0))
        if storage_candidates and chargeable and not firm_forced:
            soc = np.cumsum(np.where(net > 0.0, net * sqrt_eta, net / sqrt_eta))
            reservoir = float(np.max(np.maximum.accumulate(soc) - soc))
            if reservoir > 0.0:
                for k, t in storage_candidates.items():
                    if float(t.get("capex_usd_kwh", 0.0)) <= 0.0:
                        continue  # a zero-capex tier would look free and build an absurd reservoir
                    priced = {**t, "duration_hr": reservoir / max(peak, 0.5)}  # energy = reservoir at power = peak
                    c = _annual_fixed_cost_storage(priced, discount, max(peak, 0.5))
                    if c < cost_stor:
                        cost_stor, tier_key = c, k

        grew = "none"
        if tier_key is not None and cost_stor <= cost_firm:
            tier = storage_candidates[tier_key]
            old_power = float(tier.get("power_gw", 0.0))
            new_power = max(old_power, peak * 1.05)
            tier["power_gw"] = new_power
            tier["duration_hr"] = max(
                float(tier.get("duration_hr", _DEFAULT_LONG_DURATION_HR)),
                reservoir * 1.15 / max(new_power, 1.0),
            )
            added[tier_key] = added.get(tier_key, 0.0) + (new_power - old_power)
            for g in vre_expandable:  # extra surplus so the store can actually charge
                cf = max(float(np.mean(year_profile.solar_cf if g == "solar" else year_profile.wind_cf)), 0.03)
                _apply(g, (reservoir * 1.5 / max(len(vre_expandable), 1)) / (cf * hours))
            grew = "storage"
        elif firm_choice is not None:
            _apply(firm_choice, firm_gw * 1.05)  # small overshoot so the last bit closes cleanly
            if firm_choice not in checked_firm:
                auto_firm_gw += firm_gw * 1.05
            grew = "firm"
        else:
            break  # no dispatchable plant in the fleet and storage cannot help — a physical limit

        # Growth that stops cutting unserved (< 0.1% improvement, a scale-free test that holds on a
        # 2 TWh island and a 9000 TWh grid alike) means this lever is saturated. If it was storage,
        # fall back to firm (which can always cover the peak); if it was firm, it is a genuine plateau.
        if total_gwh >= prev * 0.999:
            if grew == "storage":
                firm_forced = True
            else:
                break
        prev = total_gwh

    unserved_twh = float(np.sum(_hourly_unserved(capacities, tiers))) / 1000.0
    added = {key: value for key, value in added.items() if value > 1e-6}

    _FIRM_LABEL = {"gas_ccgt": "gas", "coal": "coal", "nuclear": "nuclear", "other": "thermal", "hydro": "hydro"}
    note = ""
    if unserved_twh > _EXPANSION_UNSERVED_TOL_TWH:
        # Only reachable at a true physical plateau: no dispatchable plant to grow and storage that
        # cannot charge. This is a limit of the fleet, not a setting to toggle.
        note = (f"{unserved_twh:.2f} TWh/yr can't be served — adding capacity no longer adds "
                "generation (the renewables are fully curtailed and no dispatchable plant can grow).")
    elif auto_firm_gw > 0.5 and firm_choice is not None:
        label = _FIRM_LABEL.get(firm_choice, firm_choice)
        note = (f"Reached 100% by adding {auto_firm_gw:.0f} GW of {label} — the least-cost firming "
                f"for the worst lull. Check {label} in the merit list (or cap its max CF) to steer the mix.")
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


# Generators eligible for the clean-energy subsidy (ITC / PTC) — the low-carbon set. Deliberately
# broader than the RPS "renewable" set (``VRE_GENERATORS`` = solar + wind): clean energy = renewable
# + nuclear, so nuclear earns the clean subsidy but does not count toward a renewable-portfolio target.
_CLEAN_GENERATORS = {"solar", "wind_onshore", "wind_offshore", "nuclear"}

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

    Only the imported share of each generator's fuel bears the tariff: the effective surcharge is
    ``tariff × import_fuel_fraction`` (UN-Comtrade-derived, 1.0 for older profiles without the
    field), so a domestic-coal or fuel-exporting grid is untouched by design.
    """
    tariff = max(0.0, float(tariff_fraction))
    if tariff <= 0.0:
        return
    for name, cfg in profile["generators"].items():
        if name in _IMPORTED_FUEL_GENERATORS and float(cfg.get("fuel_usd_mmbtu", 0.0)) > 0.0:
            # import_fuel_fraction is a share of GENERATION; the tariff applies to fuel COST.
            # For the mixed "other" bucket the fuel price is already scaled by fossil_fraction,
            # so the imported share of the *cost* is import_fuel_fraction ÷ fossil_fraction
            # (identical for gas/coal, whose fossil_fraction is 1).
            imported = float(cfg.get("import_fuel_fraction", 1.0))
            fossil = float(cfg.get("fossil_fraction", 1.0))
            cost_share = min(1.0, imported / fossil) if fossil > 0.0 else 0.0
            cfg["fuel_usd_mmbtu"] = float(cfg["fuel_usd_mmbtu"]) * (1.0 + tariff * cost_share)


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
        key=lambda name: ["solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "hydro", "other", name].index(name)
        if name in {"solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "hydro", "other"}
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
        "ess_phs_gwh": ess_metrics["ess_phs_gwh"],
        "ess_phs_gw": ess_metrics["ess_phs_gw"],
        "ess_phs_lcoe": ess_metrics["ess_phs_lcoe"],
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
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
    ramp_up: dict[str, float] | None = None,
    ramp_down: dict[str, float] | None = None,
    ess_short_power_gw: float | None = None,
    ess_short_duration_hr: float | None = None,
    ess_long_power_gw: float | None = None,
    ess_long_duration_hr: float | None = None,
    ess_phs_power_gw: float | None = None,
    ess_phs_duration_hr: float | None = None,
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
        profile, ess_short_power_gw, ess_short_duration_hr, ess_long_power_gw, ess_long_duration_hr,
        ess_phs_power_gw, ess_phs_duration_hr,
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

    # Capacity expansion: grow the fleet to meet 100% of load, cheapest-first. "Meet 100% load" is a
    # hard promise, so this runs whenever it is checked — even with no lever selected, the guaranteed
    # firm closer serves the peak (expandable just names the resources the user prefers to grow).
    expansion: dict[str, Any] | None = None
    if meet_full_load and normalized_capacities:
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
            min_cf=min_cf,
            max_cf=max_cf,
            ramp_up=ramp_up,
            ramp_down=ramp_down,
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
            "requested": list(expandable or []),
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
        min_cf=min_cf,
        max_cf=max_cf,
        ramp_up=ramp_up,
        ramp_down=ramp_down,
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
    # "Other" is a mixed bucket (hydro/bioenergy/geothermal/oil): only its fossil slice burns
    # imported fuel, so each generator's share is weighted by its profile-declared
    # import_fuel_fraction (1.0 for gas/coal and for older profiles without the field).
    current["import_dependency"] = sum(
        _median_metric(dispatch_summary, "realized_share", gen)
        * float(profile["generators"].get(gen, {}).get("import_fuel_fraction", 1.0))
        for gen in _IMPORTED_FUEL_GENERATORS
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
    # System Radar: six trilemma axes scored from the numbers assembled above (see core.radar).
    result["radar"] = build_radar(result)
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
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
    ramp_up: dict[str, float] | None = None,
    ramp_down: dict[str, float] | None = None,
    ess_short_power_gw: float | None = None,
    ess_short_duration_hr: float | None = None,
    ess_long_power_gw: float | None = None,
    ess_long_duration_hr: float | None = None,
    ess_phs_power_gw: float | None = None,
    ess_phs_duration_hr: float | None = None,
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
        min_cf=min_cf,
        max_cf=max_cf,
        ramp_up=ramp_up,
        ramp_down=ramp_down,
        ess_short_power_gw=ess_short_power_gw,
        ess_short_duration_hr=ess_short_duration_hr,
        ess_long_power_gw=ess_long_power_gw,
        ess_long_duration_hr=ess_long_duration_hr,
        ess_phs_power_gw=ess_phs_power_gw,
        ess_phs_duration_hr=ess_phs_duration_hr,
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
    expandable: list[str] | None = None,
    meet_full_load: bool = False,
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
        expandable: Generators (and/or ``"storage"``) the solver may grow at each milestone year to
            meet 100% of load, cheapest-first, on top of the interpolated target capacities. Only
            applied when ``meet_full_load`` is True. The reported ``capacities_gw`` for each year is
            then the interpolated target plus what was added.
        meet_full_load: Enable the per-year capacity expansion described above.
        **calculate_kwargs: Forwarded verbatim to :func:`calculate_system_lcoe` (carbon price,
            demand, capacities, and ensemble are supplied per-year and must not be duplicated here).

    Returns:
        ``{"country", "years", "steps": [{year, fraction, carbon_price, annual_demand_twh,
        system_lcoe, annual_emissions_mtco2, emission_intensity, import_dependency, unserved_twh,
        capacities_gw, added_capacities_gw}, ...]}`` — one step per milestone year.
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
            expandable=expandable,
            meet_full_load=meet_full_load,
            **calculate_kwargs,
        )
        # When expansion is on, the built fleet is the interpolated target plus what the solver
        # added to meet load, so report the post-expansion capacities (and what was added).
        expansion = result.get("expansion") or {}
        built = result.get("capacities_gw") or capacities
        steps.append({
            "year": year,
            "fraction": round(fraction, 4),
            "carbon_price": round(carbon_price, 2),
            "annual_demand_twh": result["annual_demand_twh"],
            "system_lcoe": result["system_lcoe"],
            "annual_emissions_mtco2": result["annual_emissions_mtco2"],
            "emission_intensity": result["emission_intensity"],
            "import_dependency": result["import_dependency"],
            "unserved_twh": result.get("unserved_twh", 0.0),
            "capacities_gw": {key: round(float(value), 3) for key, value in built.items()},
            "added_capacities_gw": {
                key: round(float(value), 3)
                for key, value in (expansion.get("added_capacities_gw") or {}).items()
                if value > 1e-6
            },
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
    ess_phs_power_gw: float | None = None,
    ess_phs_duration_hr: float | None = None,
    max_gw: float | None = None,
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
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
                ess_phs_power_gw=ess_phs_power_gw, ess_phs_duration_hr=ess_phs_duration_hr,
                min_cf=min_cf, max_cf=max_cf,
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


def size_mix_for_adequacy(
    country: str,
    capacities: dict[str, float],
    expandable: list[str],
    lole_target_hours: float,
    carbon_price: float = 0.0,
    annual_demand_twh: float | None = None,
    ensemble: Any | None = None,
    ess_short_power_gw: float | None = None,
    ess_short_duration_hr: float | None = None,
    ess_long_power_gw: float | None = None,
    ess_long_duration_hr: float | None = None,
    ess_phs_power_gw: float | None = None,
    ess_phs_duration_hr: float | None = None,
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Least-cost *mix* that holds resource adequacy to ``lole_target_hours``.

    Where :func:`size_for_adequacy` grows a single resource, this co-sizes the whole expandable
    mix. It first runs the meet-100%-load expansion — which already picks a least-cost blend of
    generators and storage to firm the worst weather sample — then scales that blend down by a
    single factor and bisects the factor until the **ensemble** LOLE just meets the standard. So
    the blend's composition comes from the screening expansion and only its overall size is tuned
    to the (looser than zero-unserved) reliability target, which is cheaper than firming the worst
    case outright.

    Note: the gas : storage : … ratio is fixed by the worst-case expansion, not re-optimised for
    the LOLE target, so this is a screening approximation, not a full co-optimisation.

    Returns:
        Dict with ``requested`` (the expandable set), ``added_capacities_gw`` (the sized blend,
        which may include ``storage`` / ``storage_long`` power), ``scale`` (fraction of the full
        expansion), ``baseline_lole_hours``, ``lole_hours``, ``lole_target_hours``, ``met``,
        ``system_lcoe`` and ``annual_system_cost_usd_billion``.
    """
    base = {key: max(0.0, float(value)) for key, value in capacities.items()}
    target = max(0.0, float(lole_target_hours))
    base_short = float(ess_short_power_gw or 0.0)
    base_phs = float(ess_phs_power_gw or 0.0)
    base_long = float(ess_long_power_gw or 0.0)

    # 1. Full least-cost expansion to zero unserved on the worst weather sample.
    full = calculate_system_lcoe(
        country=country, shares=base, capacities_gw=base, carbon_price=carbon_price,
        annual_demand_twh=annual_demand_twh, ensemble=ensemble,
        ess_short_power_gw=ess_short_power_gw, ess_short_duration_hr=ess_short_duration_hr,
        ess_long_power_gw=ess_long_power_gw, ess_long_duration_hr=ess_long_duration_hr,
        ess_phs_power_gw=ess_phs_power_gw, ess_phs_duration_hr=ess_phs_duration_hr,
        expandable=expandable, meet_full_load=True, min_cf=min_cf, max_cf=max_cf,
    )
    added_full = dict((full.get("expansion") or {}).get("added_capacities_gw", {}))

    cache: dict[float, tuple[float, dict[str, Any]]] = {}

    def evaluate(scale: float) -> tuple[float, dict[str, Any]]:
        key = round(scale, 4)
        if key not in cache:
            caps = {
                gen: base.get(gen, 0.0) + scale * added_full.get(gen, 0.0)
                for gen in set(base) | set(added_full)
                if gen not in ("storage", "storage_phs", "storage_long")
            }
            result = calculate_system_lcoe(
                country=country, shares=caps, capacities_gw=caps, carbon_price=carbon_price,
                annual_demand_twh=annual_demand_twh, ensemble=ensemble,
                ess_short_power_gw=base_short + scale * added_full.get("storage", 0.0),
                ess_short_duration_hr=ess_short_duration_hr,
                ess_long_power_gw=base_long + scale * added_full.get("storage_long", 0.0),
                ess_long_duration_hr=ess_long_duration_hr,
                ess_phs_power_gw=base_phs + scale * added_full.get("storage_phs", 0.0),
                ess_phs_duration_hr=ess_phs_duration_hr,
                min_cf=min_cf, max_cf=max_cf,
            )
            adequacy = result.get("adequacy")
            lole = float(adequacy["lole_hours"]) if adequacy else 0.0
            cache[key] = (lole, result)
        return cache[key]

    baseline_lole, baseline_result = evaluate(0.0)

    def _report(scale: float, met: bool) -> dict[str, Any]:
        lole, result = evaluate(scale)
        return {
            "requested": list(expandable or []),
            "added_capacities_gw": {
                gen: round(scale * value, 2) for gen, value in added_full.items() if scale * value > 0.01
            },
            "scale": round(scale, 4),
            "baseline_lole_hours": round(baseline_lole, 2),
            "lole_hours": round(lole, 2),
            "lole_target_hours": target,
            "met": met,
            "system_lcoe": result["system_lcoe"],
            "annual_system_cost_usd_billion": result["annual_system_cost_usd_billion"],
        }

    if baseline_lole <= target:
        return _report(0.0, met=True)  # already adequate — no build needed
    if not added_full:
        return _report(0.0, met=False)  # nothing expandable can firm it

    # The meet-100%-load blend is sized for the worst net-load PEAK; block-bootstrap droughts can
    # need more, so let the scale grow above 1 (double until the standard is met, or give up).
    hi = 1.0
    for _ in range(_SIZE_ADEQUACY_MAX_DOUBLINGS):
        if evaluate(hi)[0] <= target:
            break
        hi *= 2.0
    if evaluate(hi)[0] > target:
        return _report(hi, met=False)  # standard unreachable by scaling this blend

    # Bisect [0, hi] for the smallest blend that meets the standard.
    lo = 0.0
    for _ in range(_SIZE_ADEQUACY_BISECT_ITERS):
        mid = (lo + hi) / 2.0
        if evaluate(mid)[0] <= target:
            hi = mid
        else:
            lo = mid
    return _report(hi, met=True)
