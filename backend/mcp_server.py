"""PowerROM MCP server — exposes the reduced-order electricity-system model as agent tools.

Wraps the same engine the web app uses (``backend.core.lcoe_engine``) in-process, so an AI agent
can list countries, price a generation mix, size for reliability, and run decarbonisation
pathways without going through the HTTP API. Tools return a compact scalar summary by default,
and can return the **full** output on request — the complete 8760-hour dispatch, the full
Load-Duration-Curve, the per-generator metric bands, and every resolved input (via the
``include_hourly`` / ``include_ldc`` / ``include_inputs`` / ``full`` flags on calculate_lcoe, and
by default on run_dispatch).

Run (stdio transport):
    python -m backend.mcp_server

Register with Claude Code via .mcp.json (see repo root).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from backend.api.countries import countries as _countries_route
from backend.core.completeness_checker import validate_generator_config as _validate_config
from backend.core.curve_fitter import fit_curve as _fit_curve
from backend.core.lcoe_engine import (
    calculate_system_lcoe,
    load_country_profile,
    simulate_pathway,
    size_for_adequacy,
    size_mix_for_adequacy,
)

# This server runs over stdio; the API no longer serves it over HTTP (see backend/main.py). The
# Streamable-HTTP settings below are inert under stdio and are kept so the server can be mounted
# over HTTP again without rework: stateless_http drops persistent sessions, streamable_http_path
# ="/" stops the path doubling to /mcp/mcp when mounted at /mcp, and DNS-rebinding protection is
# off because that guard is for local-only servers and rejects a hosted origin's Host header.
mcp = FastMCP(
    "powerrom",
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

GENERATORS = ["solar", "wind_onshore", "wind_offshore", "gas_ccgt", "coal", "nuclear", "other"]


def _medians(group: dict[str, Any]) -> dict[str, float]:
    return {k: round(v["median"], 4) for k, v in (group or {}).items() if isinstance(v, dict) and "median" in v}


def _dispatch_metrics(result: dict[str, Any]) -> dict[str, Any]:
    """Compact hourly-dispatch summary: per-generator medians + scalars + a Load-Duration-Curve
    digest — never the raw 8760-hour arrays."""
    metrics = (result.get("dispatch") or {}).get("metrics") or {}
    scalars = {k: round(v["median"], 4) for k, v in (metrics.get("scalars") or {}).items()
               if isinstance(v, dict) and "median" in v}
    ldc = result.get("ldc") or {}
    ldc_series = ldc.get("series") or {}
    net = (ldc_series.get("net_load") or {}).get("median") or []
    ldc_digest = {
        "net_load_peak_gw": round(max(net), 2) if net else None,
        "net_load_min_gw": round(min(net), 2) if net else None,
        "hours": len(net) or None,
    }
    return {
        "per_generator": {
            "capacity_factor": _medians(metrics.get("capacity_factor")),
            "energy_twh": _medians(metrics.get("energy_twh")),
            "capacity_gw": _medians(metrics.get("capacity_gw")),
            "generation_share": _medians(metrics.get("realized_share")),
        },
        "scalars": scalars,
        "load_duration_curve": ldc_digest,
    }


def _ensemble(method: str, n_samples: int, sigma: float, seed: int) -> dict[str, Any] | None:
    """Build the ensemble-settings dict, or None for a single deterministic run."""
    if method == "single":
        return None
    return {"method": method, "n_samples": n_samples, "sigma": sigma, "seed": seed}


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
    # System Radar: six 0–100 trilemma axes (+ WEC-style pillars) with the sourced raw value
    # and scoring anchor behind each score, plus the country's real-mix baseline polygon.
    if result.get("radar"):
        summary["radar"] = result["radar"]
    if result.get("expansion"):
        summary["expansion"] = result["expansion"]
        added = (result["expansion"].get("added_capacities_gw") or {})
        if added.get("storage") or added.get("storage_long"):
            # Storage duration (h) = total energy ÷ total power for each tier; added energy is the
            # added power × that duration. This is the ESS the expansion had to build to meet load.
            short_dur = (result["ess_short_gwh"] / result["ess_short_gw"]) if result.get("ess_short_gw") else 4.0
            long_dur = (result["ess_long_gwh"] / result["ess_long_gw"]) if result.get("ess_long_gw") else 168.0
            short_p, long_p = added.get("storage", 0.0), added.get("storage_long", 0.0)
            summary["required_ess_addition"] = {
                "short_power_gw": round(short_p, 2), "short_energy_gwh": round(short_p * short_dur, 1),
                "long_power_gw": round(long_p, 2), "long_energy_gwh": round(long_p * long_dur, 1),
                "total_power_gw": round(short_p + long_p, 2),
                "total_energy_gwh": round(short_p * short_dur + long_p * long_dur, 1),
            }
    return summary


def _round(x: Any, nd: int = 4) -> Any:
    """Recursively round floats in nested dict/list structures (keeps full data, trims JSON size)."""
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in x.items()}
    if isinstance(x, list):
        return [_round(v, nd) for v in x]
    return x


def _full_dispatch_metrics(result: dict[str, Any]) -> dict[str, Any]:
    """The complete per-generator dispatch metrics with p10/median/p90 bands (not just medians)."""
    return _round((result.get("dispatch") or {}).get("metrics") or {})


def _ldc(result: dict[str, Any], nd: int = 2) -> dict[str, Any] | None:
    """Full 8760-point Load-Duration-Curve as the median line per series (the curve itself); the
    p10/median/p90 bands are collapsed to the median to keep the payload usable."""
    ldc = result.get("ldc")
    if not ldc:
        return None
    series = {
        k: _round(v.get("median") if isinstance(v, dict) else v, nd)
        for k, v in (ldc.get("series") or {}).items()
    }
    return {"x_percent": _round(ldc.get("x_percent"), 3), "series": series,
            "resource_order": ldc.get("resource_order")}


def _resolved_inputs(country: str, result: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Echo every setting used: the resolved fleet, demand, all non-null parameters, and the full
    (custom-merged) country technology profile the model actually ran on."""
    profile = load_country_profile(country)
    if params.get("custom_params"):
        from backend.core.lcoe_engine import deep_merge
        profile = deep_merge(profile, params["custom_params"])
    return {
        "country": country,
        "effective_capacities_gw": {k: round(v, 3) for k, v in (result.get("capacities_gw") or {}).items()},
        "annual_demand_twh": round(result["annual_demand_twh"], 2),
        "parameters": {k: v for k, v in params.items() if v is not None and v != {} and v != []},
        "country_profile": profile,
    }


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
    shares: dict[str, float] | None = None,
    carbon_price: float = 40.0,
    annual_demand_twh: float | None = None,
    ev_penetration: float = 0.0,
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
    ramp_up: dict[str, float] | None = None,
    ramp_down: dict[str, float] | None = None,
    generator_order: list[str] | None = None,
    ess_short_power_gw: float | None = None,
    ess_short_duration_hr: float | None = None,
    ess_phs_power_gw: float | None = None,
    ess_phs_duration_hr: float | None = None,
    ess_long_power_gw: float | None = None,
    ess_long_duration_hr: float | None = None,
    expandable: list[str] | None = None,
    meet_full_load: bool = False,
    rps_target_share: float | None = None,
    rps_penalty_usd_mwh: float | None = None,
    subsidy_itc_pct: float | None = None,
    subsidy_ptc_usd_mwh: float | None = None,
    fuel_import_tariff_pct: float | None = None,
    demand_pattern: str = "default",
    demand_peak_ratio: float | None = None,
    dispatch_mode: str = "data",
    ensemble_method: str = "single",
    ensemble_samples: int = 5,
    ensemble_sigma: float = 0.04,
    ensemble_seed: int = 42,
    weather_years: list[int] | None = None,
    custom_params: dict[str, Any] | None = None,
    include_dispatch: bool = False,
    include_ldc: bool = False,
    include_hourly: bool = False,
    include_inputs: bool = False,
    full: bool = False,
) -> dict[str, Any]:
    """Price an electricity system: run the hourly dispatch for a country's generation fleet and
    return system LCOE ($/MWh), emission intensity, curtailment, unserved energy, import
    dependency, per-technology LCOE and the realised generation mix — with every policy lever the
    app supports (carbon price, EV load, min/max CF limits, storage, capacity expansion, a
    renewable-portfolio-standard target, clean-energy subsidies, and a fuel-import tariff).

    Generators: solar, wind_onshore, wind_offshore, gas_ccgt, coal, nuclear, hydro, other. Storage key is
    "storage". Give either capacities_gw (GW) or shares (fractions); omit both to use the country's
    real installed fleet.

    Args:
        country: ISO-2 code (e.g. "KR").
        capacities_gw: Installed capacity per generator (GW). Defaults to the real fleet.
        shares: Generation shares per generator (used only when capacities_gw is omitted).
        carbon_price: Carbon price, USD/tCO2 (0-500).
        annual_demand_twh: Annual demand to serve (TWh). Defaults to the country's real demand.
        ev_penetration: Fraction of the vehicle fleet electrified (0-0.5).
        min_cf / max_cf: Per-generator must-run floor / availability-ceiling capacity factor (0-1).
        ramp_up / ramp_down: Per-generator ramp limits as a fraction of nameplate per hour — the
            most a flexible thermal may change output between adjacent hours, e.g.
            ramp_up={"coal": 0.4} lets coal move 40%/h. Slow units then can't chase the evening
            solar cliff, pushing that work onto gas/storage (or unserved). Absent generators ramp
            freely; setting either switches to a sequential ramp-constrained dispatch.
        generator_order: Manual merit order override (list of generator keys).
        ess_short_power_gw / ess_short_duration_hr: Intraday battery power (GW) and duration (h).
        ess_phs_power_gw / ess_phs_duration_hr: Pumped-hydro power (GW) and duration (h).
        ess_long_power_gw / ess_long_duration_hr: Seasonal storage power (GW) and duration (h).
        expandable: Generators (and/or "storage") the solver may grow to meet 100% of load.
        meet_full_load: Grow the expandable resources cheapest-first until load is met.
        rps_target_share: Renewable-portfolio-standard target VRE share (0-1); requires a penalty.
        rps_penalty_usd_mwh: Shortfall penalty for missing the RPS target (USD/MWh).
        subsidy_itc_pct: Investment tax credit as a fraction of capex for clean tech (0-1).
        subsidy_ptc_usd_mwh: Production tax credit for clean generation (USD/MWh).
        fuel_import_tariff_pct: Surcharge on imported fuel cost (0-3 = up to +300%).
        demand_pattern: "default", "winter_peak", "summer_peak" or "flat".
        demand_peak_ratio: Peak-to-mean demand ratio (>1) to reshape the load.
        dispatch_mode: "data" (real weather-year profiles) or "parametric" (synthetic curves).
        ensemble_method: "single", "jitter", "multiyear" or "block_bootstrap" (for p10-p90 bands).
        ensemble_samples / ensemble_sigma / ensemble_seed: Ensemble configuration.
        weather_years: Weather years to use in data mode (e.g. [2018, 2019]).
        custom_params: Deep-merged overrides onto the country profile (e.g. per-generator costs).
        include_dispatch: Add the full per-generator dispatch metrics (CF, energy, shares — with
            p10/median/p90 bands) and the LDC digest.
        include_ldc: Add the complete Load-Duration-Curve (8760 sorted points per series).
        include_hourly: Add the complete chronological hourly generation (8760 h per generator).
        include_inputs: Echo every resolved setting — the effective fleet, demand, all parameters,
            and the full (custom-merged) country technology profile the model ran on.
        full: Shorthand for include_dispatch = include_ldc = include_hourly = include_inputs = True
            (returns everything: hourly, LDC, all metrics, and all inputs).
    """
    include_dispatch = include_dispatch or full
    include_ldc = include_ldc or full
    include_hourly = include_hourly or full
    include_inputs = include_inputs or full
    profile_caps = load_country_profile(country.upper()).get("capacities_gw")
    caps = capacities_gw if capacities_gw is not None else (None if shares else profile_caps)
    result = calculate_system_lcoe(
        country=country.upper(),
        shares=(shares or caps or {}),
        capacities_gw=caps,
        carbon_price=carbon_price,
        ev_penetration=ev_penetration,
        annual_demand_twh=annual_demand_twh,
        custom_params=custom_params,
        dispatch_mode=dispatch_mode,
        weather_years=weather_years,
        ensemble=_ensemble(ensemble_method, ensemble_samples, ensemble_sigma, ensemble_seed),
        include_ldc=(include_dispatch or include_ldc or include_hourly),
        generator_order=generator_order,
        min_cf=min_cf,
        max_cf=max_cf,
        ramp_up=ramp_up,
        ramp_down=ramp_down,
        ess_short_power_gw=ess_short_power_gw,
        ess_short_duration_hr=ess_short_duration_hr,
        ess_phs_power_gw=ess_phs_power_gw,
        ess_phs_duration_hr=ess_phs_duration_hr,
        ess_long_power_gw=ess_long_power_gw,
        ess_long_duration_hr=ess_long_duration_hr,
        demand_pattern=demand_pattern,
        demand_peak_ratio=demand_peak_ratio,
        expandable=expandable,
        meet_full_load=meet_full_load,
        rps_target_share=rps_target_share,
        rps_penalty_usd_mwh=rps_penalty_usd_mwh,
        subsidy_itc_pct=subsidy_itc_pct,
        subsidy_ptc_usd_mwh=subsidy_ptc_usd_mwh,
        fuel_import_tariff_pct=fuel_import_tariff_pct,
    )
    summary = _summarize_calculation(result)
    if include_dispatch:
        summary["dispatch"] = _dispatch_metrics(result)
        summary["dispatch_metrics_full"] = _full_dispatch_metrics(result)
    if include_ldc:
        summary["load_duration_curve"] = _ldc(result)
    if include_hourly:
        summary["hourly_generation"] = _round(result.get("chronological"), 2)
    if result.get("adequacy"):
        summary["adequacy"] = _round(result["adequacy"])
    if include_inputs:
        summary["inputs"] = _resolved_inputs(country.upper(), result, {
            "capacities_gw": capacities_gw, "shares": shares, "carbon_price": carbon_price,
            "annual_demand_twh": annual_demand_twh, "ev_penetration": ev_penetration,
            "min_cf": min_cf, "max_cf": max_cf, "ramp_up": ramp_up, "ramp_down": ramp_down,
            "generator_order": generator_order,
            "ess_short_power_gw": ess_short_power_gw, "ess_short_duration_hr": ess_short_duration_hr,
            "ess_phs_power_gw": ess_phs_power_gw, "ess_phs_duration_hr": ess_phs_duration_hr,
            "ess_long_power_gw": ess_long_power_gw, "ess_long_duration_hr": ess_long_duration_hr,
            "expandable": expandable, "meet_full_load": meet_full_load,
            "rps_target_share": rps_target_share, "rps_penalty_usd_mwh": rps_penalty_usd_mwh,
            "subsidy_itc_pct": subsidy_itc_pct, "subsidy_ptc_usd_mwh": subsidy_ptc_usd_mwh,
            "fuel_import_tariff_pct": fuel_import_tariff_pct, "demand_pattern": demand_pattern,
            "demand_peak_ratio": demand_peak_ratio, "dispatch_mode": dispatch_mode,
            "ensemble_method": ensemble_method, "weather_years": weather_years,
            "custom_params": custom_params,
        })
    return summary


@mcp.tool()
def run_dispatch(
    country: str,
    capacities_gw: dict[str, float] | None = None,
    carbon_price: float = 40.0,
    annual_demand_twh: float | None = None,
    ess_short_power_gw: float | None = None,
    ess_long_power_gw: float | None = None,
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
    ramp_up: dict[str, float] | None = None,
    ramp_down: dict[str, float] | None = None,
    dispatch_mode: str = "data",
    ensemble_method: str = "single",
    ensemble_samples: int = 5,
    include_hourly: bool = True,
    include_ldc: bool = True,
) -> dict[str, Any]:
    """Run the 8760-hour dispatch and return the FULL result: per-generator metrics with
    p10/median/p90 bands (capacity factor, energy, generation/capacity share, per-generator
    curtailment), the scalar metrics, the complete Load-Duration-Curve (8760 sorted points per
    series) and the complete chronological hourly generation (8760 h per generator, plus demand
    and storage), and the resource order.

    Args:
        country: ISO-2 code.
        capacities_gw: Installed capacity per generator (GW). Defaults to the real fleet.
        carbon_price: Carbon price, USD/tCO2.
        annual_demand_twh: Demand to serve (TWh); defaults to the real demand.
        ess_short_power_gw / ess_long_power_gw: Storage power (GW).
        min_cf / max_cf: Per-generator CF limits (0-1) — must-run floor / availability ceiling,
            e.g. max_cf={"gas_ccgt": 0.2} caps gas at a 20% CF in the dispatch.
        ramp_up / ramp_down: Per-generator ramp limits (fraction of nameplate per hour) — the most a
            flexible thermal may change output between adjacent hours, e.g. ramp_up={"coal": 0.4}.
            Absent generators ramp freely.
        dispatch_mode: "data" or "parametric".
        ensemble_method: "single", "jitter", "multiyear" or "block_bootstrap".
        ensemble_samples: Ensemble sample count.
        include_hourly: Include the 8760-hour chronological generation (large). Default True.
        include_ldc: Include the full 8760-point Load-Duration-Curve. Default True.
    """
    caps = capacities_gw or load_country_profile(country.upper()).get("capacities_gw")
    result = calculate_system_lcoe(
        country=country.upper(), shares=caps or {}, capacities_gw=caps,
        carbon_price=carbon_price, annual_demand_twh=annual_demand_twh,
        dispatch_mode=dispatch_mode, include_ldc=True, min_cf=min_cf, max_cf=max_cf,
        ramp_up=ramp_up, ramp_down=ramp_down,
        ensemble=_ensemble(ensemble_method, ensemble_samples, 0.04, 42),
        ess_short_power_gw=ess_short_power_gw, ess_long_power_gw=ess_long_power_gw,
    )
    out: dict[str, Any] = {
        "country": result["country"],
        "system_lcoe_usd_mwh": round(result["system_lcoe"], 2),
        "emission_intensity_gco2_kwh": round(result["emission_intensity"] * 1000, 1),
        "annual_demand_twh": round(result["annual_demand_twh"], 2),
        "capacities_gw": {k: round(v, 3) for k, v in (result.get("capacities_gw") or {}).items()},
        "metrics": _full_dispatch_metrics(result),
        "digest": _dispatch_metrics(result),
    }
    if include_ldc:
        out["load_duration_curve"] = _ldc(result)
    if include_hourly:
        out["hourly_generation"] = _round(result.get("chronological"), 2)
    return out


@mcp.tool()
def lcoe_vs_vre_curve(
    country: str,
    carbon_price: float = 40.0,
    steps: int = 8,
    max_vre_share: float = 0.9,
    dispatch_mode: str = "parametric",
) -> dict[str, Any]:
    """Trace the LCOE-vs-renewable-share frontier: sweep the variable-renewable (solar+wind) share
    from 0 up to max_vre_share and, at each step, run the full model to return system LCOE,
    emission intensity and curtailment. The non-VRE mix keeps the country's real proportions
    (scaled down), and the VRE is split by the country's real solar/wind ratio. Useful for finding
    the cost-optimal renewable share.

    Args:
        country: ISO-2 code.
        carbon_price: Carbon price, USD/tCO2.
        steps: Number of points along the sweep (each is a full dispatch, so keep modest).
        max_vre_share: Highest VRE share to sweep to (0-1).
        dispatch_mode: "data" (real weather, slower) or "parametric".
    """
    base = load_country_profile(country.upper()).get("shares", {})
    non_vre = {k: max(base.get(k, 0.0), 0.0) for k in ("gas_ccgt", "coal", "nuclear", "other")}
    nv_total = sum(non_vre.values()) or 1.0
    vre_keys = ("solar", "wind_onshore", "wind_offshore")
    vre_base = {k: max(base.get(k, 0.0), 0.0) for k in vre_keys}
    vb_total = sum(vre_base.values())
    split = ({k: vre_base[k] / vb_total for k in vre_keys} if vb_total > 0
             else {"solar": 0.6, "wind_onshore": 0.4, "wind_offshore": 0.0})

    curve: list[dict[str, float]] = []
    for i in range(steps + 1):
        vre = round(max_vre_share * i / steps, 4)
        shares = {k: vre * split[k] for k in vre_keys}
        for k, w in non_vre.items():
            shares[k] = (1.0 - vre) * w / nv_total
        r = calculate_system_lcoe(country=country.upper(), shares=shares,
                                  carbon_price=carbon_price, dispatch_mode=dispatch_mode)
        curve.append({
            "vre_share": vre,
            "system_lcoe_usd_mwh": round(r["system_lcoe"], 2),
            "emission_intensity_gco2_kwh": round(r["emission_intensity"] * 1000, 1),
            "curtailment_rate": round(r["curtailment_rate"], 4),
        })
    return {"country": country.upper(), "curve": curve}


@mcp.tool()
def validate_generator_config(generator_config: dict[str, Any]) -> dict[str, Any]:
    """Validate a generator configuration, reporting which fields are fitted, defaulted or missing
    per component — the same check the app runs before accepting a custom profile.

    Args:
        generator_config: A {generator: {field: value}} config (e.g. a profile's "generators").
    """
    return _validate_config(generator_config)


@mcp.tool()
def fit_curve(
    data_points: list[list[float]],
    func_type: str,
    bounds: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """Fit a parametric curve to (x, y) data points and return the fitted parameters, R-squared and
    95% confidence intervals — used to derive capacity-factor / efficiency / cost functions.

    Args:
        data_points: List of [x, y] pairs.
        func_type: One of the allowed function types (e.g. "linear", "logarithmic", "power",
            "quadratic", "constant", "piecewise").
        bounds: Optional {param: [lo, hi]} bounds on the fit.
    """
    r = _fit_curve(
        data_points=[(float(x), float(y)) for x, y in data_points],
        func_type=func_type, bounds=bounds,
    )
    return {
        "params": r.params, "r_squared": round(r.r_squared, 4),
        "confidence_intervals": {k: list(v) for k, v in r.confidence_intervals.items()},
        "sufficient_data": r.sufficient_data, "error_message": r.error_message,
    }


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
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
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
        min_cf / max_cf: Per-generator CF limits applied during sizing (must-run floor /
            availability ceiling, 0-1) — e.g. max_cf={"gas_ccgt": 0.2} caps gas at a 20% CF.
    """
    return size_for_adequacy(
        country=country.upper(), capacities=capacities_gw, firm_key=firm_key,
        lole_target_hours=lole_target_hours, carbon_price=carbon_price,
        annual_demand_twh=annual_demand_twh,
        ess_short_power_gw=ess_short_power_gw, ess_long_power_gw=ess_long_power_gw,
        min_cf=min_cf, max_cf=max_cf,
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
    min_cf: dict[str, float] | None = None,
    max_cf: dict[str, float] | None = None,
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
        min_cf / max_cf: Per-generator CF limits applied throughout co-sizing (0-1). E.g.
            max_cf={"gas_ccgt": 0.2} caps gas at a 20% CF so the solver builds solar/wind/storage
            to cover the reliability gap the capped peaker leaves — the direct "gas as peaker" knob.
    """
    return size_mix_for_adequacy(
        country=country.upper(), capacities=capacities_gw, expandable=expandable,
        lole_target_hours=lole_target_hours, carbon_price=carbon_price,
        annual_demand_twh=annual_demand_twh,
        ess_short_power_gw=ess_short_power_gw, ess_long_power_gw=ess_long_power_gw,
        min_cf=min_cf, max_cf=max_cf,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
