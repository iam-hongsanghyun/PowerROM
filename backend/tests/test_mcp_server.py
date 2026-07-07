"""The PowerROM MCP server must register the expected tools and return compact, correct summaries.

Skipped automatically where the optional ``mcp`` SDK is not installed (it is a local-tool
dependency, kept out of the lean serverless bundle — see requirements-mcp.txt).
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

from backend import mcp_server as srv  # noqa: E402

_EXPECTED_TOOLS = {
    "list_countries", "get_country_profile", "calculate_lcoe", "run_dispatch",
    "lcoe_vs_vre_curve", "validate_generator_config", "fit_curve",
    "simulate_decarbonisation_pathway", "size_firm_capacity_for_reliability",
    "size_least_cost_mix_for_reliability",
}


def test_all_tools_registered() -> None:
    names = {t.name for t in asyncio.run(srv.mcp.list_tools())}
    assert _EXPECTED_TOOLS.issubset(names), f"missing tools: {_EXPECTED_TOOLS - names}"


def test_list_countries_returns_real_data() -> None:
    out = srv.list_countries()
    assert out["count"] >= 30
    codes = {c["code"] for c in out["countries"]}
    assert {"KR", "US", "DE"}.issubset(codes)
    kr = next(c for c in out["countries"] if c["code"] == "KR")
    assert kr["annual_demand_twh"] > 0 and kr["capacities_gw"]


def test_calculate_lcoe_summary_shape_and_sanity() -> None:
    r = srv.calculate_lcoe("KR", carbon_price=50.0, dispatch_mode="parametric")
    assert 30.0 <= r["system_lcoe_usd_mwh"] <= 400.0
    assert r["emission_intensity_gco2_kwh"] >= 0.0
    assert "generation_shares" in r and "lcoe_by_generator" in r
    # No raw hourly arrays leak into the compact summary.
    assert all(k not in r for k in ("dispatch", "ldc", "chronological", "curve_data"))


def test_calculate_supports_all_policy_levers() -> None:
    r = srv.calculate_lcoe(
        "DE", carbon_price=90.0, ev_penetration=0.2, subsidy_itc_pct=0.3,
        rps_target_share=0.7, rps_penalty_usd_mwh=60.0, fuel_import_tariff_pct=0.5,
        min_cf={"nuclear": 0.8}, max_cf={"coal": 0.5}, ess_short_power_gw=5.0,
        dispatch_mode="parametric", include_dispatch=True,
    )
    assert r["system_lcoe_usd_mwh"] > 0
    assert set(r["dispatch"]) == {"per_generator", "scalars", "load_duration_curve"}
    assert r["dispatch"]["load_duration_curve"]["net_load_peak_gw"] is not None


def test_run_dispatch_returns_full_hourly_and_ldc() -> None:
    d = srv.run_dispatch("US", carbon_price=50.0, dispatch_mode="parametric")
    assert d["system_lcoe_usd_mwh"] > 0
    # Full 8760-hour chronological generation per generator.
    assert len(d["hourly_generation"]["series"]["solar"]) == 8760
    # Full 8760-point Load-Duration-Curve, net load descending.
    nl = d["load_duration_curve"]["series"]["net_load"]
    assert len(nl) == 8760 and nl[0] >= nl[-1]
    # Full per-generator metric bands + the compact digest.
    assert "capacity_factor" in d["metrics"] and "scalars" in d["digest"]


def test_calculate_full_output_has_everything() -> None:
    r = srv.calculate_lcoe("KR", carbon_price=50.0, dispatch_mode="parametric", full=True)
    assert len(r["hourly_generation"]["series"]["solar"]) == 8760       # full hourly
    assert len(r["load_duration_curve"]["series"]["net_load"]) == 8760   # full LDC
    assert "capacity_factor" in r["dispatch_metrics_full"]               # full metric bands
    assert "country_profile" in r["inputs"] and r["inputs"]["parameters"]  # all resolved inputs
    # Summary stays compact when full is not requested.
    lean = srv.calculate_lcoe("KR", carbon_price=50.0, dispatch_mode="parametric")
    assert "hourly_generation" not in lean and "load_duration_curve" not in lean


def test_lcoe_vs_vre_curve_is_a_real_sweep() -> None:
    out = srv.lcoe_vs_vre_curve("KR", carbon_price=50.0, steps=4, dispatch_mode="parametric")
    curve = out["curve"]
    assert len(curve) == 5
    assert [p["vre_share"] for p in curve] == sorted(p["vre_share"] for p in curve)  # ascending
    # Emissions fall as the VRE share rises.
    assert curve[0]["emission_intensity_gco2_kwh"] > curve[-1]["emission_intensity_gco2_kwh"]


def test_validate_and_fit_tools() -> None:
    prof = srv.get_country_profile("KR")
    assert "status" in srv.validate_generator_config(prof["generators"])
    fit = srv.fit_curve([[0.1, 0.2], [0.3, 0.18], [0.5, 0.15], [0.7, 0.12]], "linear")
    assert fit["r_squared"] > 0.9 and "a" in fit["params"]


def test_pathway_tool_runs_with_expansion() -> None:
    out = srv.simulate_decarbonisation_pathway(
        country="KR",
        target_capacities_gw={"solar": 26.68, "wind_onshore": 2.26, "nuclear": 26.05,
                              "coal": 0.0, "gas_ccgt": 0.0, "other": 8.0},
        years=[2025, 2050],
        expandable=["solar", "wind_onshore", "nuclear", "storage"],
        meet_full_load=True,
        ess_short_power_gw=10.0, ess_long_power_gw=5.0,
    )
    assert out["steps"][-1]["unserved_twh"] < 1.0  # expansion met the load after phase-out
