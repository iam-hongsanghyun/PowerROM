from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from backend.core.hourly_profiles import HOURS_PER_YEAR, EnsembleSettings, YearProfile

VRE_GENERATORS = ("solar", "wind_onshore", "wind_offshore")
DISPLAY_ORDER = ("solar", "wind_onshore", "wind_offshore", "nuclear", "coal", "gas_ccgt", "other")
QUANTILES = (0.1, 0.5, 0.9)

# Short-duration storage arbitrages only in the priciest hours (top 1−percentile of the marginal
# thermal cost), so its discharge aligns with the demand/price peak — where the reliability need
# also is — instead of depleting itself in cheaper pre-peak hours and under-firming the peak.
_ARBITRAGE_PRICE_PERCENTILE: float = 75.0


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
    # Net storage flow, GW: positive = discharging (serving load), negative = charging.
    storage_net_gw: np.ndarray | None = None


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
    if power_gw <= 0.0 or energy_gwh <= 0.0:
        return np.zeros(hours, dtype=float), np.zeros(hours, dtype=float)

    # Iterate over Python floats (lists), not numpy element indexing, which is ~5-10x faster
    # for a scalar sequential loop of this length — this dispatch runs thousands of times per
    # capacity-expansion solve, so it dominates runtime.
    surplus = surplus_gw.tolist()
    deficit = deficit_gw.tolist()
    charge = [0.0] * hours
    discharge = [0.0] * hours
    soc = 0.0
    eff = max(1e-6, float(efficiency))
    power = float(power_gw)
    energy = float(energy_gwh)
    for h in range(hours):
        s = surplus[h]
        if s > 1e-9 and soc < energy:
            c = s if s < power else power
            room = energy - soc
            if room < c:
                c = room
            charge[h] = c
            soc += c
        else:
            d0 = deficit[h]
            if d0 > 1e-9 and soc > 1e-9:
                cap = soc * eff
                d = d0 if d0 < power else power
                if cap < d:
                    d = cap
                discharge[h] = d
                soc -= d / eff
    return np.asarray(charge, dtype=float), np.asarray(discharge, dtype=float)


def _simulate_storage_economic(
    surplus_gw: np.ndarray,
    unserved_gw: np.ndarray,
    displaceable_gw: np.ndarray,
    power_gw: float,
    energy_gwh: float,
    efficiency: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Chronological SoC dispatch of one storage device, dispatched *economically*.

    Generalises :func:`_simulate_storage_soc` from reliability-only to reliability + arbitrage.
    Each hour, in order: charge from free ``surplus`` (curtailed VRE); otherwise discharge first
    to any ``unserved`` demand (reliability, top priority), then — with any remaining power and
    charge — to displace the most-expensive running thermal (``displaceable``), cutting that
    generator's fuel and carbon. Charging stays free-surplus only, so displacing thermal is
    always profitable; its *value* (the displaced fuel + carbon) rises with the carbon price,
    which is how storage now responds to carbon rather than only to shortfalls. Passing
    ``displaceable_gw = 0`` recovers pure reliability behaviour (used for the long/seasonal tier,
    whose charge is held as a drought reserve rather than cycled for daily arbitrage).

    Args:
        surplus_gw: Hourly chargeable surplus (curtailed generation), GW.
        unserved_gw: Hourly unserved demand — reliability discharge target, GW.
        displaceable_gw: Hourly output of the marginal (priciest running) thermal, GW.
        power_gw: Rated charge/discharge power, GW.
        energy_gwh: Usable energy capacity (= power × duration), GWh.
        efficiency: Round-trip efficiency (energy delivered ÷ energy stored), 0–1.

    Returns:
        ``(charge_gw, discharge_to_unserved_gw, discharge_to_thermal_gw)`` hourly arrays.

    Algorithm:
        Greedy causal loop with state of charge ``soc``; a discharging hour serves unserved
        before displacing thermal:
        charge ``c = min(surplus, power, energy − soc)`` → ``soc += c``; else
        ``d_u = min(unserved, power, soc·η)`` then ``d_t = min(displaceable, power − d_u, soc·η)``,
        ``soc −= (d_u + d_t)/η``.
    """
    hours = len(surplus_gw)
    if power_gw <= 0.0 or energy_gwh <= 0.0:
        z = np.zeros(hours, dtype=float)
        return z, z.copy(), z.copy()

    surplus = surplus_gw.tolist()
    unserved = unserved_gw.tolist()
    displaceable = displaceable_gw.tolist()
    charge = [0.0] * hours
    discharge_unserved = [0.0] * hours
    discharge_thermal = [0.0] * hours
    soc = 0.0
    eff = max(1e-6, float(efficiency))
    power = float(power_gw)
    energy = float(energy_gwh)
    for h in range(hours):
        s = surplus[h]
        if s > 1e-9 and soc < energy:
            c = s if s < power else power
            room = energy - soc
            if room < c:
                c = room
            charge[h] = c
            soc += c
            continue
        if soc <= 1e-9:
            continue
        headroom = power
        u = unserved[h]
        if u > 1e-9:  # reliability first
            cap = soc * eff
            d = u if u < headroom else headroom
            if cap < d:
                d = cap
            discharge_unserved[h] = d
            soc -= d / eff
            headroom -= d
        if headroom > 1e-9 and soc > 1e-9:  # then displace the priciest thermal
            m = displaceable[h]
            if m > 1e-9:
                cap = soc * eff
                d = m if m < headroom else headroom
                if cap < d:
                    d = cap
                discharge_thermal[h] = d
                soc -= d / eff
    return (
        np.asarray(charge, dtype=float),
        np.asarray(discharge_unserved, dtype=float),
        np.asarray(discharge_thermal, dtype=float),
    )


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
    economic_storage: bool = True,
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
    ramp_up: dict[str, float] | None = None,
    ramp_down: dict[str, float] | None = None,
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

    Per-generator capacity-factor limits (``min_cf`` / ``max_cf``, each a ``{generator: cf}``
    mapping in [0, 1]) refine the stack:

    * ``max_cf`` — availability ceiling. A generator can never dispatch above
      ``capacity × max_cf`` (planned outages, fuel/water limits, grid-connection caps). For VRE
      it clips the resource; the excess is curtailed.
    * ``min_cf`` — must-run floor. A generator runs at **at least** ``capacity × min_cf`` every
      hour, dispatched ahead of the merit order; any floor output above the residual load is
      spilled (curtailed). This is the general take-or-pay / must-run rule the nuclear baseload
      is a special case of. When ``min_cf`` for nuclear is given it overrides the profile
      ``cf_base`` baseload level.

    Both default to no effect when absent, so a call without them dispatches exactly as before.

    Ramp limits (``ramp_up`` / ``ramp_down``, each a ``{generator: fraction_of_capacity_per_hour}``
    mapping) add inter-hour coupling on the flexible thermals: a unit's output may change by at most
    ``capacity × rate`` between adjacent hours. When either is given the flexible fill switches from
    the vectorized per-hour merit order to a sequential pass — a unit that cannot ramp up fast enough
    leaves residual for storage/unserved to absorb, and one that cannot ramp down fast enough is held
    above load (the excess spilled). A generator absent from the mapping is unconstrained. Absent
    entirely, the fast vectorized fill is kept and results are identical to before.
    """
    generator_names = _ordered_generators(profile, generator_order)
    normalized_shares = _normalize_shares(shares)
    fixed_capacities = _normalize_capacities(capacities_gw or {})
    min_cf = {k: max(0.0, min(1.0, float(v))) for k, v in (min_cf or {}).items()}
    max_cf = {k: max(0.0, min(1.0, float(v))) for k, v in (max_cf or {}).items()}
    # Ramp rates: fraction of nameplate a unit can move per hour (>1 ⇒ effectively unconstrained).
    ramp_up = {k: max(0.0, float(v)) for k, v in (ramp_up or {}).items()}
    ramp_down = {k: max(0.0, float(v)) for k, v in (ramp_down or {}).items()}
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
            if gen == "solar":
                cf = year_profile.solar_cf
            elif gen == "wind_offshore":
                # Offshore shares the wind weather shape but at its own (higher) mean CF: scale the
                # onshore wind profile so its mean equals the offshore base CF.
                base = _base_capacity_factor(profile["generators"][gen], fallback=0.42)
                mean_wind = max(float(np.mean(year_profile.wind_cf)), 1e-6)
                cf = np.clip(year_profile.wind_cf * (base / mean_wind), 0.0, 1.0)
            else:
                cf = year_profile.wind_cf
            if gen in fixed_capacities:
                capacities_gw[gen] = fixed_capacities[gen]
            else:
                mean_cf = max(float(np.mean(cf)), 1e-6)
                target_gwh = annual_demand_twh * 1000 * normalized_shares.get(gen, 0.0)
                capacities_gw[gen] = target_gwh / (mean_cf * hours) if target_gwh > 0 else 0.0
            available_gw[gen] = capacities_gw[gen] * cf
            continue

        if gen == "nuclear":
            # min_cf overrides the profile baseload level; nuclear runs flat at this CF.
            nuclear_cf = min_cf.get(
                "nuclear", _base_capacity_factor(profile["generators"][gen], fallback=0.85)
            )
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

    # Availability ceiling: no generator may dispatch above capacity × max_cf. For VRE this
    # clips the resource (surplus curtailed downstream); for thermals it caps the fleet.
    for gen in generator_names:
        if gen in max_cf:
            available_gw[gen] = np.minimum(available_gw[gen], capacities_gw[gen] * max_cf[gen])

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
    residual_after_vre = residual_gw.copy()  # net load left for flexibles; the ramp pass re-fills it

    # 2.5 Must-run floors (min_cf): each flexible thermal runs at least capacity × min_cf every
    #     hour, ahead of the merit order. Floor output above the residual is spilled (curtailed).
    for gen in flexible_names:
        floor_cf = min_cf.get(gen, 0.0)
        if floor_cf <= 0.0:
            continue
        floor_gw = np.minimum(np.full(hours, capacities_gw[gen] * floor_cf), available_gw[gen])
        served = np.minimum(floor_gw, residual_gw)
        dispatch_gw[gen] = floor_gw
        curtailed_gw[gen] = floor_gw - served
        residual_gw = np.maximum(residual_gw - served, 0.0)

    # 3. Flexible thermals fill the remaining residual in merit order, using the headroom above
    #    any must-run floor already dispatched. With no floors this is the plain merit fill.
    for gen in flexible_names:
        headroom = np.maximum(available_gw[gen] - dispatch_gw[gen], 0.0)
        added = np.minimum(residual_gw, headroom)
        dispatch_gw[gen] = dispatch_gw[gen] + added
        residual_gw = np.maximum(residual_gw - added, 0.0)

    # 3.5 Ramp limits (opt-in): re-dispatch the flexibles sequentially so each unit's output moves
    #     by at most capacity × ramp rate between adjacent hours. A unit that can't ramp *up* fast
    #     enough leaves residual for storage/unserved to absorb; one that can't ramp *down* fast
    #     enough is forced to keep running above load (spilled). Seeded from the unconstrained hour-0
    #     dispatch so there is no spurious cold-start at midnight Jan 1. Skipped (keeping the fast
    #     vectorized fill above) when no ramp rate is given, so default behaviour is unchanged.
    if (ramp_up or ramp_down) and flexible_names:
        prev = {gen: float(dispatch_gw[gen][0]) for gen in flexible_names}
        ru = {gen: capacities_gw[gen] * ramp_up.get(gen, math.inf) for gen in flexible_names}
        rd = {gen: capacities_gw[gen] * ramp_down.get(gen, math.inf) for gen in flexible_names}
        avail_l = {gen: available_gw[gen].tolist() for gen in flexible_names}
        floor_l = {
            gen: np.minimum(capacities_gw[gen] * min_cf.get(gen, 0.0), available_gw[gen]).tolist()
            for gen in flexible_names
        }
        disp_l = {gen: [0.0] * hours for gen in flexible_names}
        curt_l = {gen: [0.0] * hours for gen in flexible_names}
        res_l = residual_after_vre.tolist()
        unserved_l = [0.0] * hours
        for h in range(hours):
            want = res_l[h]
            for gen in flexible_names:
                a = avail_l[gen][h]
                p = prev[gen]
                lo = floor_l[gen][h]
                ramp_floor = p - rd[gen]
                if ramp_floor > lo:
                    lo = ramp_floor
                if lo > a:
                    lo = a
                if lo < 0.0:
                    lo = 0.0
                hi = p + ru[gen]
                if hi > a:
                    hi = a
                if hi < lo:
                    hi = lo
                d = want
                if d < lo:
                    d = lo
                elif d > hi:
                    d = hi
                served = d if d < want else want
                disp_l[gen][h] = d
                curt_l[gen][h] = d - served
                want -= served
                prev[gen] = d
            unserved_l[h] = want if want > 0.0 else 0.0
        for gen in flexible_names:
            dispatch_gw[gen] = np.array(disp_l[gen], dtype=float)
            curtailed_gw[gen] = np.array(curt_l[gen], dtype=float)
        residual_gw = np.array(unserved_l, dtype=float)

    unserved_gw = residual_gw

    # 4. Endogenous storage (user-set tiers). Charge from curtailed surplus, discharge to unserved.
    #    When ``economic_storage`` (reporting mode), the short/intraday tier also discharges to
    #    displace the priciest running thermal in the top-price hours (arbitrage), cutting its fuel
    #    + carbon; the long/seasonal tier holds its charge as a drought reserve (no arbitrage).
    #    Reliability-only mode (``economic_storage=False``, used for expansion sizing) skips the
    #    arbitrage so a storage device's firm contribution is measured without depleting it.
    storage_net_gw = np.zeros(hours, dtype=float)  # + = discharging, − = charging
    if storage_tiers:
        curtailed_total = sum(curtailed_gw.values(), np.zeros(hours, dtype=float))
        surplus_gw = curtailed_total.copy()
        deficit_gw = unserved_gw.copy()

        # Per-hour marginal (priciest running) thermal + its price, for arbitrage displacement.
        marginal_gw = np.zeros(hours, dtype=float)
        marginal_idx = np.full(hours, -1, dtype=int)
        displaceable_peak = np.zeros(hours, dtype=float)
        if economic_storage and flexible_names:
            flex_by_cost = sorted(
                flexible_names,
                key=lambda gen: _marginal_cost_usd_mwh(profile["generators"][gen], carbon_price),
            )
            price = np.zeros(hours, dtype=float)
            for i, gen in enumerate(flex_by_cost):  # ascending SRMC → last running one is priciest
                running = dispatch_gw[gen] > 1e-9
                srmc = _marginal_cost_usd_mwh(profile["generators"][gen], carbon_price)
                marginal_idx = np.where(running, i, marginal_idx)
                marginal_gw = np.where(running, dispatch_gw[gen], marginal_gw)
                price = np.where(running, srmc, price)
            positive = price[price > 1e-9]
            if positive.size:
                threshold = float(np.percentile(positive, _ARBITRAGE_PRICE_PERCENTILE))
                displaceable_peak = np.where(price >= threshold, marginal_gw, 0.0)
                # Reserve storage for reliability: on any day with a pre-storage shortfall, hold the
                # charge for the peak instead of arbitraging it away (which would re-open the gap the
                # expansion sized storage to close). Arbitrage runs only on days with no firmness need.
                if hours % 24 == 0:
                    critical_day = (deficit_gw.reshape(-1, 24).max(axis=1) > 1e-9)
                    displaceable_peak = np.where(np.repeat(critical_day, 24), 0.0, displaceable_peak)

        for tier in storage_tiers:
            power = float(tier.get("power_gw", 0.0))
            energy = power * float(tier.get("duration_hr", 0.0))
            displaceable = displaceable_peak if tier.get("name") == "short" else np.zeros(hours, dtype=float)
            charge, discharge_unserved, discharge_thermal = _simulate_storage_economic(
                surplus_gw, deficit_gw, displaceable, power, energy, float(tier.get("efficiency", 0.85))
            )
            surplus_gw = surplus_gw - charge
            deficit_gw = deficit_gw - discharge_unserved
            if discharge_thermal.any():
                # Reduce the displaced marginal thermal's output (and expose the next tier to the
                # now-lower margin), so its fuel + carbon fall in the reported LCOE.
                for i, gen in enumerate(flex_by_cost):
                    dispatch_gw[gen] = dispatch_gw[gen] - np.where(marginal_idx == i, discharge_thermal, 0.0)
                displaceable_peak = np.maximum(displaceable_peak - discharge_thermal, 0.0)
            storage_net_gw += discharge_unserved + discharge_thermal - charge
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
        storage_net_gw=storage_net_gw,
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
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
    ramp_up: dict[str, float] | None = None,
    ramp_down: dict[str, float] | None = None,
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
            min_cf=min_cf,
            max_cf=max_cf,
            ramp_up=ramp_up,
            ramp_down=ramp_down,
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
        # Raw hourly unserved per scenario, for resource-adequacy metrics (LOLE / EUE).
        summary["member_unserved_gw"] = [result.unserved_gw for result in results]
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
    # True served load = demand − unserved. By the dispatch energy balance this equals the sum of
    # generation PLUS storage discharge, so storage discharge stacks on top of generation to reach
    # this line. (Storage discharge that displaces thermal already reduced that thermal's dispatch.)
    served_load = np.maximum(result.demand_gw - result.unserved_gw, 0.0)
    storage_net = result.storage_net_gw if result.storage_net_gw is not None else np.zeros(hours)

    series: dict[str, np.ndarray] = {
        "demand": result.demand_gw[order],
        "net_load": net_load[order],
        "served_load": served_load[order],
        "curtailed_vre": sum(
            (result.curtailed_gw.get(gen, np.zeros(hours)) for gen in VRE_GENERATORS),
            np.zeros(hours),
        )[order],
        "unserved": result.unserved_gw[order],
        # Net storage flow (+ discharging serves load, − charging), sorted by the same gross-load
        # order as every other series so it stacks position-for-position.
        "storage": storage_net[order],
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
    if result.storage_net_gw is not None:
        # Net storage: + discharging (stacks with generation), − charging (below zero).
        series["storage"] = _round_list(result.storage_net_gw, 2)
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
