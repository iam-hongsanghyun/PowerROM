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
    "list_countries", "get_country_profile", "calculate_lcoe",
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
