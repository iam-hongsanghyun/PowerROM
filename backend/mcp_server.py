"""PowerROM MCP server — exposes the reduced-order electricity-system model as agent tools.

Wraps the same engine the web app uses (``backend.core.lcoe_engine``) in-process, so an AI agent
can list countries, price a generation mix, size for reliability, and run decarbonisation
pathways without going through the HTTP API. Returns compact JSON summaries (scalars + small
maps) rather than raw 8760-hour arrays, so results fit an agent's context.

Run (stdio transport):
    python -m backend.mcp_server

Register with Claude Code via .mcp.json (see repo root).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from backend.api.countries import countries as _countries_route
from backend.core.lcoe_engine import (
    calculate_system_lcoe,
    load_country_profile,
    simulate_pathway,
    size_for_adequacy,
    size_mix_for_adequacy,
)

mcp = FastMCP("powerrom")

GENERATORS = ["solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "other"]


def _summarize_calculation(result: dict[str, Any]) -> dict[str, Any]:
    """Pick the decision-relevant scalars/maps from a full engine result (drop hourly arrays)."""
    dispatch = result.get("dispatch") or {}
    metrics = dispatch.get("metrics") or {}
    realized = {k: round(v["median"], 4) for k, v in (metrics.get("realized_share") or {}).items()}
    summary: dict[str, Any] = {
        "country": result["country"],
        "system_lcoe_usd_mwh": round(result["system_lcoe"], 2),
        "system_lcoe_p10_p90": [result.get("system_lcoe_p10"), result.get("system_lcoe_p90")],
        "emission_intensity_tco2_mwh": round(result["emission_intensity"], 4),
        "emission_intensity_gco2_kwh": round(result["emission_intensity"] * 1000, 1),
        "annual_emissions_mtco2": round(result["annual_emissions_mtco2"], 1),
        "annual_system_cost_usd_billion": round(result["annual_system_cost_usd_billion"], 2),
        "annual_demand_twh": round(result["annual_demand_twh"], 1),
        "import_dependency": round(result["import_dependency"], 4),
        "curtailment_rate": round(result["curtailment_rate"], 4),
        "unserved_twh": round(result["unserved_twh"], 3),
        "capacities_gw": {k: round(v, 2) for k, v in (result.get("capacities_gw") or {}).items()},
        "generation_shares": realized or {k: round(v, 4) for k, v in result["shares"].items()},
        "storage": {
            "short_gw": round(result.get("ess_short_gw", 0.0), 2),
            "short_gwh": round(result.get("ess_short_gwh", 0.0), 1),
            "long_gw": round(result.get("ess_long_gw", 0.0), 2),
            "long_gwh": round(result.get("ess_long_gwh", 0.0), 1),
        },
        "lcoe_by_generator": {
            gen: {k: (round(v, 3) if isinstance(v, (int, float)) else v) for k, v in vals.items()}
            for gen, vals in (result.get("lcoe_by_generator") or {}).items()
        },
    }
    if result.get("expansion"):
        summary["expansion"] = result["expansion"]
    return summary


@mcp.tool()
def list_countries() -> dict[str, Any]:
    """List every modelled country with its real Ember data: code, name, data year, annual demand
    (TWh), installed capacity by technology (GW) and the default generation mix (shares)."""
    resp = _countries_route().model_dump()
    items = [
        {
            "code": c["code"], "name": c["name"], "data_year": c.get("data_year"),
            "annual_demand_twh": c.get("annual_demand_twh") or c["annual_generation_twh"],
            "annual_generation_twh": c["annual_generation_twh"],
            "capacities_gw": c.get("capacities_gw", {}), "shares": c.get("shares", {}),
        }
        for c in resp["countries"]
    ]
    return {"count": len(items), "countries": items}


@mcp.tool()
def get_country_profile(country: str) -> dict[str, Any]:
    """Full technology profile for a country: per-generator capex/opex, fuel price, heat rate,
    emission factor and capacity factor, the storage tiers, discount rate, and data sources.

    Args:
        country: ISO-2 country code (e.g. "KR", "US", "DE").
    """
    return load_country_profile(country.upper())


@mcp.tool()
def calculate_lcoe(
    country: str,
    capacities_gw: dict[str, float] | None = None,
    carbon_price: float = 40.0,
    annual_demand_twh: float | None = None,
    ev_penetration: float = 0.0,
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
    ess_short_power_gw: float | None = None,
    ess_long_power_gw: float | None = None,
    expandable: list[str] | None = None,
    meet_full_load: bool = False,
    dispatch_mode: str = "data",
) -> dict[str, Any]:
    """Price an electricity system: run the hourly dispatch for a country's generation fleet and
    return system LCOE ($/MWh), emission intensity, curtailment, unserved energy, import
    dependency, per-technology LCOE, and the realised generation mix.

    Generators are: solar, wind_onshore, gas_ccgt, coal, nuclear, other. Omit capacities_gw to use
    the country's real installed fleet.

    Args:
        country: ISO-2 code (e.g. "KR").
        capacities_gw: Installed capacity per generator in GW. Defaults to the country's real fleet.
        carbon_price: Carbon price in USD/tCO2 (0-500).
        annual_demand_twh: Annual demand to serve (TWh). Defaults to the country's real demand.
        ev_penetration: Fraction of the vehicle fleet electrified (0-0.5).
        min_cf: Per-generator must-run floor capacity factor (0-1).
        max_cf: Per-generator availability-ceiling capacity factor (0-1).
        ess_short_power_gw: Short-duration (intraday battery) storage power, GW.
        ess_long_power_gw: Long-duration (seasonal) storage power, GW.
        expandable: Generators (and/or "storage") the solver may grow to meet 100% of load.
        meet_full_load: If true, grow the expandable resources cheapest-first until load is met.
        dispatch_mode: "data" (real weather-year profiles) or "parametric" (synthetic curves).
    """
    caps = capacities_gw or load_country_profile(country.upper()).get("capacities_gw")
    result = calculate_system_lcoe(
        country=country.upper(),
        shares=caps or {},
        capacities_gw=caps,
        carbon_price=carbon_price,
        ev_penetration=ev_penetration,
        annual_demand_twh=annual_demand_twh,
        dispatch_mode=dispatch_mode,
        min_cf=min_cf,
        max_cf=max_cf,
        ess_short_power_gw=ess_short_power_gw,
        ess_long_power_gw=ess_long_power_gw,
        expandable=expandable,
        meet_full_load=meet_full_load,
    )
    return _summarize_calculation(result)


@mcp.tool()
def simulate_decarbonisation_pathway(
    country: str,
    target_capacities_gw: dict[str, float],
    years: list[int],
    start_capacities_gw: dict[str, float] | None = None,
    carbon_price_start: float = 40.0,
    carbon_price_end: float = 150.0,
    annual_demand_twh_start: float | None = None,
    annual_demand_twh_end: float | None = None,
    expandable: list[str] | None = None,
    meet_full_load: bool = False,
    ess_short_power_gw: float | None = None,
    ess_long_power_gw: float | None = None,
) -> dict[str, Any]:
    """Run a planning pathway from today's fleet to a target-year mix, interpolating capacities,
    an escalating carbon price and demand, and returning system LCOE, emission intensity, import
    dependency and (optionally) the capacity the solver built at each milestone year.

    Args:
        country: ISO-2 code.
        target_capacities_gw: End-of-horizon installed capacity per generator (GW); set a
            generator to 0 to phase it out.
        years: Ascending milestone years, e.g. [2025, 2035, 2050].
        start_capacities_gw: Today's fleet (GW). Defaults to the country's real fleet.
        carbon_price_start / carbon_price_end: Carbon price (USD/tCO2) at the first / last year.
        annual_demand_twh_start / _end: Demand (TWh) at the first / last year.
        expandable: Generators (and/or "storage") the solver may grow to meet load each year.
        meet_full_load: Enable per-year capacity expansion of the expandable resources.
        ess_short_power_gw / ess_long_power_gw: Storage power (GW) available for the expansion.
    """
    start = start_capacities_gw or load_country_profile(country.upper()).get("capacities_gw", {})
    return simulate_pathway(
        country=country.upper(),
        start_capacities=start,
        target_capacities=target_capacities_gw,
        years=sorted(years),
        carbon_price_start=carbon_price_start,
        carbon_price_end=carbon_price_end,
        annual_demand_twh_start=annual_demand_twh_start,
        annual_demand_twh_end=annual_demand_twh_end,
        expandable=expandable,
        meet_full_load=meet_full_load,
        ess_short_power_gw=ess_short_power_gw,
        ess_long_power_gw=ess_long_power_gw,
    )


@mcp.tool()
def size_firm_capacity_for_reliability(
    country: str,
    capacities_gw: dict[str, float],
    firm_key: str,
    lole_target_hours: float = 2.4,
    carbon_price: float = 40.0,
    annual_demand_twh: float | None = None,
    ess_short_power_gw: float | None = None,
    ess_long_power_gw: float | None = None,
) -> dict[str, Any]:
    """Find the minimum amount of a single firm resource needed to meet a reliability standard
    (loss-of-load expectation ≤ target hours/year, default the 1-day-in-10-year standard of 2.4).

    Args:
        country: ISO-2 code.
        capacities_gw: The base fleet (GW).
        firm_key: The resource to grow — a generator ("gas_ccgt", "nuclear", ...) or "storage".
        lole_target_hours: Reliability target in hours/year (2.4 = 1-day-in-10-year).
        carbon_price: Carbon price (USD/tCO2).
        annual_demand_twh: Demand to serve (TWh); defaults to the country's real demand.
        ess_short_power_gw / ess_long_power_gw: Existing storage power (GW).
    """
    return size_for_adequacy(
        country=country.upper(), capacities=capacities_gw, firm_key=firm_key,
        lole_target_hours=lole_target_hours, carbon_price=carbon_price,
        annual_demand_twh=annual_demand_twh,
        ess_short_power_gw=ess_short_power_gw, ess_long_power_gw=ess_long_power_gw,
    )


@mcp.tool()
def size_least_cost_mix_for_reliability(
    country: str,
    capacities_gw: dict[str, float],
    expandable: list[str],
    lole_target_hours: float = 2.4,
    carbon_price: float = 40.0,
    annual_demand_twh: float | None = None,
    ess_short_power_gw: float | None = None,
    ess_long_power_gw: float | None = None,
) -> dict[str, Any]:
    """Co-size the least-cost combination of the selected resources to meet a reliability standard
    (LOLE ≤ target hours/year), returning the GW added per resource and the resulting LOLE.

    Args:
        country: ISO-2 code.
        capacities_gw: The base fleet (GW).
        expandable: Resources the solver may grow, e.g. ["gas_ccgt", "nuclear", "storage"].
        lole_target_hours: Reliability target in hours/year (2.4 = 1-day-in-10-year).
        carbon_price: Carbon price (USD/tCO2).
        annual_demand_twh: Demand to serve (TWh).
        ess_short_power_gw / ess_long_power_gw: Existing storage power (GW).
    """
    return size_mix_for_adequacy(
        country=country.upper(), capacities=capacities_gw, expandable=expandable,
        lole_target_hours=lole_target_hours, carbon_price=carbon_price,
        annual_demand_twh=annual_demand_twh,
        ess_short_power_gw=ess_short_power_gw, ess_long_power_gw=ess_long_power_gw,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
