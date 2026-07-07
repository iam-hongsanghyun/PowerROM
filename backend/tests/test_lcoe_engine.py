import pytest

from backend.core.lcoe_engine import (
    calculate_system_lcoe,
    simulate_pathway,
    size_for_adequacy,
    size_mix_for_adequacy,
)

_BLOCK8 = {"method": "block_bootstrap", "n_samples": 8, "sigma": 0.05, "seed": 42, "block_days": 14}


def test_size_for_adequacy_grows_firm_capacity_to_meet_lole() -> None:
    # The probabilistic analogue of meet-100%-load: grow a firm resource until the ensemble LOLE
    # reaches a reliability standard. An under-built fleet must add gas; the achieved LOLE lands
    # on the target and the required capacity exceeds the start.
    caps = {"solar": 120, "wind_onshore": 70, "gas_ccgt": 15, "coal": 6, "nuclear": 12, "other": 3}
    result = size_for_adequacy(
        country="KR", capacities=caps, firm_key="gas_ccgt", lole_target_hours=5.0,
        carbon_price=50.0, annual_demand_twh=595.0, ensemble=_BLOCK8,
        ess_short_power_gw=12.0, ess_short_duration_hr=8.0,
    )
    assert result["baseline_lole_hours"] > 5.0        # under-built to start
    assert result["required_gw"] > caps["gas_ccgt"]   # had to grow gas
    assert result["added_gw"] > 0.0
    assert result["lole_hours"] <= 5.0 + 0.1          # ...to meet the standard
    assert result["met"] is True


def test_size_mix_for_adequacy_co_sizes_a_blend_to_target() -> None:
    # Co-sizes the whole expandable mix (not one axis): scales the least-cost meet-100%-load blend
    # until the ensemble LOLE meets the standard. An under-built fleet must build, the achieved
    # LOLE lands on the target, and a baseload+peaker blend can be part of the answer.
    caps = {"solar": 160, "wind_onshore": 90, "gas_ccgt": 12, "coal": 3, "nuclear": 10, "other": 2}
    result = size_mix_for_adequacy(
        country="KR", capacities=caps, expandable=["gas_ccgt", "nuclear", "storage"],
        lole_target_hours=5.0, carbon_price=50.0, annual_demand_twh=595.0, ensemble=_BLOCK8,
        ess_short_power_gw=15.0, ess_short_duration_hr=8.0,
    )
    assert result["baseline_lole_hours"] > 5.0            # under-built to start
    assert result["lole_hours"] <= 5.0 + 0.3              # sized to the standard
    assert result["met"] is True
    assert sum(result["added_capacities_gw"].values()) > 0.0  # a real build


def test_size_for_adequacy_no_build_when_already_adequate() -> None:
    # A fleet already inside the standard needs no firm build.
    caps = {"solar": 40, "wind_onshore": 20, "gas_ccgt": 90, "coal": 20, "nuclear": 30, "other": 5}
    result = size_for_adequacy(
        country="KR", capacities=caps, firm_key="gas_ccgt", lole_target_hours=8.0,
        carbon_price=50.0, annual_demand_twh=595.0, ensemble=_BLOCK8,
    )
    assert result["baseline_lole_hours"] <= 8.0
    assert result["added_gw"] == 0.0
    assert result["met"] is True

_SINGLE = {"method": "single", "n_samples": 1, "sigma": 0.0, "seed": 42}


def test_pathway_phase_out_decarbonises_over_time() -> None:
    # A planning pathway: phase coal out and build VRE/nuclear while carbon escalates. Each
    # milestone interpolates the fleet + carbon price and runs the full model, so emissions and
    # import dependency should fall monotonically as the mix cleans up.
    start = {"solar": 30, "wind_onshore": 15, "gas_ccgt": 30, "coal": 30, "nuclear": 20, "other": 3}
    target = {"solar": 120, "wind_onshore": 70, "gas_ccgt": 25, "coal": 0, "nuclear": 25, "other": 2}
    pathway = simulate_pathway(
        country="KR", start_capacities=start, target_capacities=target,
        years=[2025, 2035, 2050], carbon_price_start=40.0, carbon_price_end=150.0, ensemble=_SINGLE,
    )
    steps = pathway["steps"]

    assert [s["year"] for s in steps] == [2025, 2035, 2050]
    assert steps[0]["capacities_gw"]["coal"] == 30.0   # today's fleet
    assert steps[-1]["capacities_gw"]["coal"] == 0.0   # ...phased out by 2050
    assert steps[-1]["carbon_price"] == 150.0          # carbon escalated to the end value
    emissions = [s["annual_emissions_mtco2"] for s in steps]
    assert emissions[0] > emissions[-1]                                  # decarbonises
    assert all(a >= b for a, b in zip(emissions, emissions[1:]))         # monotonically
    imports = [s["import_dependency"] for s in steps]
    assert all(a >= b for a, b in zip(imports, imports[1:]))             # import reliance falls too


def test_expansion_meets_full_load_and_prices_it() -> None:
    # Deliberately under-built dispatchable fleet -> large unserved energy.
    caps = {"solar": 100, "wind_onshore": 50, "gas_ccgt": 10, "coal": 5, "nuclear": 10, "other": 3}
    base = dict(country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE)

    before = calculate_system_lcoe(**base)
    after = calculate_system_lcoe(**base, expandable=["gas_ccgt", "nuclear"], meet_full_load=True)

    assert before["unserved_twh"] > 1.0  # under-built
    assert after["unserved_twh"] < 0.1   # firmed to ~100% served
    added = after["expansion"]["added_capacities_gw"]
    assert sum(added.values()) > 0.0
    assert set(added).issubset({"gas_ccgt", "nuclear"})  # only the checked generators grew


def test_short_storage_displaces_thermal_and_cuts_emissions() -> None:
    # Economic dispatch: short storage charges free surplus and discharges to displace the priciest
    # running thermal, so adding it cuts emissions even where there is little unserved to serve —
    # the old reliability-only dispatch could not (storage sat below thermals in the merit stack).
    caps = {"solar": 180, "wind_onshore": 50, "gas_ccgt": 40, "coal": 10, "nuclear": 15, "other": 3}
    base = dict(
        country="KR", shares=caps, carbon_price=80.0, capacities_gw=caps, ensemble=_SINGLE,
        annual_demand_twh=595.0, ess_short_duration_hr=6.0, ess_long_power_gw=0.0,
    )
    without = calculate_system_lcoe(**base, ess_short_power_gw=0.0)
    with_storage = calculate_system_lcoe(**base, ess_short_power_gw=25.0)

    assert with_storage["annual_emissions_mtco2"] < without["annual_emissions_mtco2"] - 1.0


def test_clean_energy_subsidy_targets_clean_only() -> None:
    caps = {"solar": 120, "wind_onshore": 60, "gas_ccgt": 30, "coal": 15, "nuclear": 18, "other": 6}
    base = dict(country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE)

    baseline = calculate_system_lcoe(**base)["system_lcoe"]
    itc = calculate_system_lcoe(**base, subsidy_itc_pct=0.30)
    ptc = calculate_system_lcoe(**base, subsidy_ptc_usd_mwh=25.0)

    assert itc["system_lcoe"] < baseline  # capex credit lowers cost
    assert ptc["system_lcoe"] < baseline  # production credit lowers cost
    # PTC is a negative $/MWh line on clean generators only.
    assert ptc["lcoe_by_generator"]["solar"]["subsidy"] < 0.0
    assert ptc["lcoe_by_generator"]["nuclear"]["subsidy"] < 0.0
    assert ptc["lcoe_by_generator"]["gas_ccgt"]["subsidy"] == 0.0


def test_fuel_import_tariff_raises_imported_fuel_only() -> None:
    # Energy-security lever: a fuel-import tariff surcharges imported-fuel generators (gas/coal/
    # other), raising their fuel cost and system LCOE, while clean generators are untouched.
    caps = {"solar": 30, "wind_onshore": 15, "gas_ccgt": 30, "coal": 25, "nuclear": 18, "other": 2}
    base = dict(country="KR", shares=caps, carbon_price=30.0, capacities_gw=caps, ensemble=_SINGLE)

    baseline = calculate_system_lcoe(**base)
    tariffed = calculate_system_lcoe(**base, fuel_import_tariff_pct=0.5)

    assert tariffed["system_lcoe"] > baseline["system_lcoe"]  # imported fuel got dearer
    fuel0 = baseline["lcoe_by_generator"]["gas_ccgt"]["fuel"]
    fuel1 = tariffed["lcoe_by_generator"]["gas_ccgt"]["fuel"]
    assert fuel1 == pytest.approx(fuel0 * 1.5, rel=1e-6)  # +50% on gas fuel
    assert tariffed["lcoe_by_generator"]["solar"]["fuel"] == 0.0  # clean generators unaffected
    assert 0.0 <= baseline["import_dependency"] <= 1.0  # energy-security metric reported


def test_rps_target_badge_and_penalty() -> None:
    caps = {"solar": 120, "wind_onshore": 60, "gas_ccgt": 30, "coal": 15, "nuclear": 18, "other": 6}
    base = dict(country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE)

    easy = calculate_system_lcoe(**base, rps_target_share=0.20)
    hard = calculate_system_lcoe(**base, rps_target_share=0.90, rps_penalty_usd_mwh=40.0)
    baseline = calculate_system_lcoe(**base)["system_lcoe"]

    assert easy["rps"]["met"] is True
    assert hard["rps"]["met"] is False
    assert hard["rps"]["shortfall_share"] > 0.0
    # Shortfall penalty raises system LCOE by penalty × shortfall.
    assert hard["rps"]["penalty_lcoe"] > 0.0
    assert hard["system_lcoe"] > baseline


def test_expansion_holds_across_weather_ensemble() -> None:
    # Sized against the worst weather sample, so 100% load holds across the whole ensemble.
    caps = {"solar": 120, "wind_onshore": 60, "gas_ccgt": 15, "coal": 8, "nuclear": 15, "other": 4}
    result = calculate_system_lcoe(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps,
        ensemble={"method": "jitter", "n_samples": 6, "sigma": 0.06, "seed": 42},
        expandable=["gas_ccgt", "nuclear"], meet_full_load=True,
    )
    band = result["dispatch"]["metrics"]["scalars"]["unserved_twh"]
    assert band["p90"] < 0.1  # ~zero unserved even in the worst sampled weather year


def test_expansion_blends_baseload_and_peaker_by_screening() -> None:
    # A wide unserved block (energy shortfall). Folding running cost into the metric pulls
    # cheap-to-run baseload into the base and leaves a cheap-to-build peaker for the top,
    # so a gas+nuclear blend beats gas-only on total system cost.
    caps = {"solar": 160, "wind_onshore": 70, "gas_ccgt": 18, "coal": 4, "nuclear": 10, "other": 3}
    base = dict(country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE)

    gas_only = calculate_system_lcoe(**base, expandable=["gas_ccgt"], meet_full_load=True)
    blend = calculate_system_lcoe(**base, expandable=["gas_ccgt", "nuclear"], meet_full_load=True)

    assert gas_only["unserved_twh"] < 0.1 and blend["unserved_twh"] < 0.1  # both firm the load
    added = blend["expansion"]["added_capacities_gw"]
    assert added.get("gas_ccgt", 0.0) > 0.0 and added.get("nuclear", 0.0) > 0.0  # a real blend
    assert blend["system_lcoe"] < gas_only["system_lcoe"]  # cheaper than peaker-only


def test_expansion_prefers_short_storage_for_diurnal_peak() -> None:
    # A diurnal (summer-evening) peak is short storage's domain. The solver must NOT jump to the
    # far dearer long tier just because one small short-power increment saturated its 12h energy
    # window and shaved ~0 — it must escalate short to its effective size and keep it.
    caps = {"solar": 130, "wind_onshore": 20, "nuclear": 74, "gas_ccgt": 6, "coal": 0, "other": 0}
    r = calculate_system_lcoe(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        annual_demand_twh=595.0, demand_pattern="summer_peak",
        ess_short_power_gw=2.0, ess_short_duration_hr=12.0, ess_long_power_gw=0.0,
        ess_long_duration_hr=168.0, expandable=["storage"], meet_full_load=True,
    )
    added = r["expansion"]["added_capacities_gw"]
    assert r["unserved_twh"] < 0.05
    assert added.get("storage", 0.0) > 0.0          # short storage firms the diurnal peak
    assert added.get("storage_long", 0.0) == 0.0    # the dear long tier is not built for it


def test_expansion_grows_storage_for_multiday_drought() -> None:
    # A near-100%-renewable fleet must ride through a multi-day winter Dunkelflaute. With both
    # storage tiers expandable the solver grows storage (short-duration wins on cost for droughts
    # up to ~9 days — it over-provisions power to buy energy more cheaply than the long tier; long
    # only wins for still-longer events) alongside a VRE overbuild to close the gap.
    caps = {"solar": 80, "wind_onshore": 40, "nuclear": 2, "coal": 1, "gas_ccgt": 3, "other": 1}
    r = calculate_system_lcoe(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        annual_demand_twh=595.0, ess_short_power_gw=30.0, ess_short_duration_hr=12.0,
        ess_long_power_gw=0.0, ess_long_duration_hr=168.0,
        expandable=["solar", "wind_onshore", "storage"], meet_full_load=True,
    )
    added = r["expansion"]["added_capacities_gw"]
    assert r["unserved_twh"] < 0.1  # the multi-day drought is firmed
    assert added.get("storage", 0.0) + added.get("storage_long", 0.0) > 0.0  # ...by growing storage
    assert any(added.get(key, 0.0) > 0.0 for key in ("solar", "wind_onshore"))  # + a VRE overbuild


def test_expansion_storage_only_built_when_it_firms_the_peak() -> None:
    # The binding peak is a low-renewable lull a 4h battery cannot cover, so short storage
    # must NOT be force-grown: the least-firm-cost expansion firms it with gas, not storage.
    caps = {"solar": 160, "wind_onshore": 70, "gas_ccgt": 18, "coal": 4, "nuclear": 10, "other": 3}
    base = dict(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        ess_short_power_gw=5.0, ess_long_power_gw=2.0,
    )
    with_both = calculate_system_lcoe(**base, expandable=["storage", "gas_ccgt"], meet_full_load=True)
    added = with_both["expansion"]["added_capacities_gw"]

    assert with_both["unserved_twh"] < 0.1                 # firmed to ~100% served
    assert added.get("gas_ccgt", 0.0) > 0.0                # gas firms the drought peak
    assert "storage" not in added                          # storage cannot firm it -> not built
    assert with_both["expansion"]["note"]                  # and the UI is told why


def test_expansion_vre_plus_storage_can_firm() -> None:
    # Renewables + storage CAN reach 100% load by overbuilding VRE to charge the storage —
    # the capability the firm-only metric used to forbid. VRE has a *threshold* response (a
    # small overbuild shaves no peak), so the solver must escalate the step to find the
    # overbuild that firms it — here a large (winter-lull-sized) capacity addition.
    caps = {"solar": 70, "wind_onshore": 31, "gas_ccgt": 27, "coal": 24, "nuclear": 14, "other": 5}
    vre = calculate_system_lcoe(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        annual_demand_twh=595.0, ess_short_power_gw=20.0, ess_short_duration_hr=12.0,
        ess_long_power_gw=5.0, expandable=["solar", "wind_onshore", "storage"], meet_full_load=True,
    )
    assert vre["unserved_twh"] < 0.1  # renewables firm the load
    added = vre["expansion"]["added_capacities_gw"]
    assert any(added.get(k, 0.0) > 0.0 for k in ("solar", "wind_onshore"))  # VRE was grown to firm it
    assert sum(added.values()) > 20.0  # firming a winter lull with VRE takes a large overbuild


def test_expansion_noop_without_a_selection() -> None:
    caps = {"solar": 100, "wind_onshore": 50, "gas_ccgt": 10, "coal": 5, "nuclear": 10, "other": 3}
    result = calculate_system_lcoe(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        expandable=[], meet_full_load=True,
    )
    # Nothing checked to expand -> no expansion is attempted at all.
    assert result.get("expansion") is None


def test_korean_default_lcoe_range() -> None:
    result = calculate_system_lcoe(
        country="KR",
        shares={
            "solar": 0.15,
            "wind_onshore": 0.1,
            "gas_ccgt": 0.3,
            "coal": 0.25,
            "nuclear": 0.18,
            "other": 0.02,
        },
        carbon_price=50.0,
    )
    assert 80 <= result["system_lcoe"] <= 150
    assert result["emission_intensity"] >= 0


def test_share_normalization_notice() -> None:
    result = calculate_system_lcoe(
        country="KR",
        shares={
            "solar": 15,
            "wind_onshore": 10,
            "gas_ccgt": 30,
            "coal": 25,
            "nuclear": 18,
            "other": 2,
        },
        carbon_price=0.0,
    )
    assert result["data_quality"]["share_normalized"] is True


def test_annual_demand_scales_total_outputs() -> None:
    low = calculate_system_lcoe(
        country="KR",
        shares={
            "solar": 0.15,
            "wind_onshore": 0.1,
            "gas_ccgt": 0.3,
            "coal": 0.25,
            "nuclear": 0.18,
            "other": 0.02,
        },
        carbon_price=50.0,
        annual_demand_twh=300,
    )
    high = calculate_system_lcoe(
        country="KR",
        shares={
            "solar": 0.15,
            "wind_onshore": 0.1,
            "gas_ccgt": 0.3,
            "coal": 0.25,
            "nuclear": 0.18,
            "other": 0.02,
        },
        carbon_price=50.0,
        annual_demand_twh=600,
    )

    assert high["system_lcoe"] == low["system_lcoe"]
    assert high["annual_system_cost_usd_billion"] > low["annual_system_cost_usd_billion"]
    assert high["annual_emissions_mtco2"] > low["annual_emissions_mtco2"]
