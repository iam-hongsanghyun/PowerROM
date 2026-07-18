import pytest

from backend.core.lcoe_engine import (
    calculate_system_lcoe,
    load_country_profile,
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


def test_size_mix_respects_max_cf_gas_cap() -> None:
    # Capping gas at a 20% CF during co-sizing is the direct "gas as peaker" knob: with gas throttled
    # the solver must build more clean capacity/storage to hold the reliability standard than it
    # would with gas unconstrained.
    caps = {"solar": 160, "wind_onshore": 90, "gas_ccgt": 40, "coal": 0, "nuclear": 10, "other": 2}
    common = dict(country="KR", capacities=caps, expandable=["solar", "wind_onshore", "storage"],
                  lole_target_hours=5.0, carbon_price=50.0, annual_demand_twh=595.0, ensemble=_BLOCK8,
                  ess_short_power_gw=15.0, ess_short_duration_hr=8.0)
    free = size_mix_for_adequacy(**common)
    capped = size_mix_for_adequacy(**common, max_cf={"gas_ccgt": 0.20})
    assert capped["met"] and free["met"]
    assert sum(capped["added_capacities_gw"].values()) > sum(free["added_capacities_gw"].values())


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


def test_pathway_capacity_expansion_meets_load_after_phase_out() -> None:
    # Phase coal + gas out entirely, then let the solver grow the selected clean resources to meet
    # 100% load each milestone year. Without expansion the final year is badly short; with it the
    # gap is closed and the built capacity is reported per year.
    start = {"solar": 30, "wind_onshore": 15, "gas_ccgt": 30, "coal": 30, "nuclear": 20, "other": 3}
    target = {"solar": 30, "wind_onshore": 15, "gas_ccgt": 0, "coal": 0, "nuclear": 20, "other": 3}
    common = dict(
        country="KR", start_capacities=start, target_capacities=target,
        years=[2025, 2050], carbon_price_start=40.0, carbon_price_end=150.0,
        annual_demand_twh_start=625.0, annual_demand_twh_end=625.0, ensemble=_SINGLE,
    )

    no_expand = simulate_pathway(**common)
    assert no_expand["steps"][-1]["unserved_twh"] > 1.0            # coal+gas gone, nothing fills it
    assert no_expand["steps"][-1]["added_capacities_gw"] == {}

    expanded = simulate_pathway(
        **common, meet_full_load=True,
        expandable=["solar", "wind_onshore", "nuclear", "storage"],
        ess_short_power_gw=10.0, ess_long_power_gw=5.0,
    )
    final = expanded["steps"][-1]
    assert final["unserved_twh"] < 0.5                             # firmed to ~100% served
    assert sum(final["added_capacities_gw"].values()) > 0.0        # capacity was built
    assert set(final["added_capacities_gw"]).issubset(            # only the checked resources grew
        {"solar", "wind_onshore", "nuclear", "storage", "storage_long"}
    )


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


def test_phs_tier_is_built_priced_and_reported() -> None:
    from backend.core.lcoe_engine import _build_storage_tiers, load_country_profile

    p = load_country_profile("KR")
    tiers = _build_storage_tiers(p, 20.0, 4.0, 5.0, 168.0, 8.0, 10.0)
    assert [t["name"] for t in tiers] == ["short", "phs", "long"]
    phs = next(t for t in tiers if t["name"] == "phs")
    assert phs["power_gw"] == 8.0 and phs["duration_hr"] == 10.0
    assert 0.70 <= phs["efficiency"] <= 0.85  # pumped-hydro round-trip (not the battery's 0.85+)
    assert phs["capex_usd_kwh"] > 0.0        # priced from the profile ess block

    caps = {"solar": 26.68, "wind_onshore": 2.26, "gas_ccgt": 50.26, "coal": 41.19,
            "nuclear": 26.05, "hydro": 1.82, "other": 6.18}
    r = calculate_system_lcoe(
        country="KR", shares={}, capacities_gw=caps, carbon_price=0.0, ensemble=_SINGLE,
        annual_demand_twh=625.38, ess_phs_power_gw=8.0, ess_phs_duration_hr=10.0,
    )
    assert r["ess_phs_gw"] == 8.0
    assert r["ess_phs_gwh"] == pytest.approx(80.0)  # 8 GW × 10 h
    assert r["ess_phs_lcoe"] > 0.0
    assert r["ess_requirement_gwh"] == pytest.approx(80.0)  # only PHS set here


def test_short_storage_displaces_thermal_and_cuts_emissions() -> None:
    # Economic dispatch: short storage charges free surplus and discharges to displace the priciest
    # running thermal, so adding it cuts emissions even where there is little unserved to serve —
    # the old reliability-only dispatch could not (storage sat below thermals in the merit stack).
    caps = {"solar": 180, "wind_onshore": 50, "gas_ccgt": 40, "coal": 10, "nuclear": 15, "other": 3}
    # Uncap the firm generators (lift the default utilization ceiling) so this isolates the
    # storage-displaces-thermal feature: with the cap, this deliberately thermal-light mix is so
    # short of firm capacity that storage serves *unserved load* instead of displacing thermal,
    # masking the emissions effect under test.
    uncap = {gen: 1.0 for gen in ("gas_ccgt", "coal", "nuclear", "other")}
    base = dict(
        country="KR", shares=caps, carbon_price=80.0, capacities_gw=caps, ensemble=_SINGLE,
        annual_demand_twh=595.0, ess_short_duration_hr=6.0, ess_long_power_gw=0.0, max_cf=uncap,
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


def test_import_dependency_weights_other_by_fossil_and_trade() -> None:
    # "Other" is a mixed bucket: only its fossil slice counts, and only to the extent the fuel
    # is actually imported (UN Comtrade net trade). Paraguay's "other" is ~pure hydro → low;
    # Kuwait's is oil-fired but burns DOMESTIC oil (net exporter) → low; Lebanon's is oil-fired
    # on imported oil → high.
    caps = {"solar": 0.3, "wind_onshore": 0.2, "gas_ccgt": 0.3, "coal": 0.2, "nuclear": 0.0, "other": 9.0}
    py = calculate_system_lcoe(country="PY", shares=caps, carbon_price=0.0, capacities_gw=caps, ensemble=_SINGLE)
    kw = calculate_system_lcoe(country="KW", shares=caps, carbon_price=0.0, capacities_gw=caps, ensemble=_SINGLE)
    lb = calculate_system_lcoe(country="LB", shares=caps, carbon_price=0.0, capacities_gw=caps, ensemble=_SINGLE)
    assert py["import_dependency"] < 0.25, "hydro-dominated 'other' misread as imported fuel"
    assert kw["import_dependency"] < 0.35, "domestic-oil 'other' misread as imported fuel"
    assert lb["import_dependency"] > 0.60, "imported-oil 'other' must count as imported fuel"


def test_fuel_import_tariff_skips_domestic_fuel() -> None:
    # Australia's power coal is domestic (top exporter; Comtrade import fraction ~0), so a fuel-
    # import tariff must leave its coal fuel cost untouched — unlike Japan's (all imported).
    caps = {"solar": 5, "wind_onshore": 5, "gas_ccgt": 10, "coal": 20, "nuclear": 0, "other": 2}
    for country, expect_ratio in (("AU", 1.0), ("JP", 1.5)):
        base = dict(country=country, shares=caps, carbon_price=0.0, capacities_gw=caps, ensemble=_SINGLE)
        fuel0 = calculate_system_lcoe(**base)["lcoe_by_generator"]["coal"]["fuel"]
        fuel1 = calculate_system_lcoe(**base, fuel_import_tariff_pct=0.5)["lcoe_by_generator"]["coal"]["fuel"]
        assert fuel1 == pytest.approx(fuel0 * expect_ratio, rel=1e-3), country


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


def test_expansion_meets_load_with_cheapest_firm() -> None:
    # LDC sizing covers the residual net-load peak with the cheapest-to-build dispatchable among the
    # checked candidates — gas here (far lower capex than nuclear) — sized directly from the peak,
    # and reaches ~100% served.
    caps = {"solar": 160, "wind_onshore": 70, "gas_ccgt": 18, "coal": 4, "nuclear": 10, "other": 3}
    base = dict(country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE)

    r = calculate_system_lcoe(**base, expandable=["gas_ccgt", "nuclear"], meet_full_load=True)

    assert r["unserved_twh"] < 0.1                         # firms the load
    added = r["expansion"]["added_capacities_gw"]
    assert added.get("gas_ccgt", 0.0) > 0.0               # cheapest firm to build covers the peak
    assert added.get("nuclear", 0.0) == 0.0               # dearer nuclear is not chosen


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


def test_expansion_per_type_phs_grows_only_phs() -> None:
    # Per-type expandable storage: checking ONLY the PHS tier makes the solver firm the peak with
    # pumped hydro (reported under "storage_phs") and leave the battery/seasonal tiers untouched.
    caps = {"solar": 130, "wind_onshore": 20, "nuclear": 74, "gas_ccgt": 6, "coal": 0, "other": 0}
    r = calculate_system_lcoe(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        annual_demand_twh=595.0, demand_pattern="summer_peak",
        ess_short_power_gw=0.0, ess_short_duration_hr=12.0,
        ess_phs_power_gw=2.0, ess_phs_duration_hr=10.0,
        ess_long_power_gw=0.0, ess_long_duration_hr=168.0,
        expandable=["storage_phs"], meet_full_load=True,
    )
    added = r["expansion"]["added_capacities_gw"]
    assert r["unserved_twh"] < 0.05
    assert added.get("storage_phs", 0.0) > 0.0     # PHS firms the diurnal peak
    assert added.get("storage", 0.0) == 0.0        # the battery tier is not built
    assert added.get("storage_long", 0.0) == 0.0   # the seasonal tier is not built


def test_expansion_note_suggests_only_unchecked_options_when_it_cannot_close() -> None:
    # VRE alone cannot firm a windless night, so a residual remains. The note must (a) report the
    # residual PRECISELY — never round a real shortfall to "0 TWh" — and (b) suggest only the options
    # the user has NOT selected: here storage and firm are both un-checked, so both are offered.
    caps = {"solar": 30, "wind_onshore": 10, "gas_ccgt": 8, "nuclear": 5, "coal": 0, "other": 2}
    r = calculate_system_lcoe(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        annual_demand_twh=595.0, ess_short_power_gw=0.0, expandable=["solar"], meet_full_load=True,
    )
    note = r["expansion"]["note"]
    assert r["unserved_twh"] > 1.0                              # a real, un-closed shortfall
    assert "0.00 TWh/yr still unserved" not in note            # not rounded/contradictory
    assert "still unserved" in note
    assert "storage tier expandable" in note                   # un-checked -> suggested
    assert "firm generator" in note                            # un-checked -> suggested


def test_expansion_note_does_not_cry_failure_when_it_closes() -> None:
    # When storage is expandable and the solver DOES reach 100% (by building a large reservoir), the
    # note must not contradict itself with a "still unserved" failure line — it reports the storage
    # cost instead, and never re-suggests making storage expandable (it already is).
    caps = {"solar": 50, "wind_onshore": 20, "gas_ccgt": 5, "coal": 0, "nuclear": 0, "other": 0}
    r = calculate_system_lcoe(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        annual_demand_twh=595.0, ess_short_power_gw=10.0, ess_short_duration_hr=4.0,
        expandable=["storage_short"], meet_full_load=True,
    )
    note = r["expansion"]["note"]
    assert r["unserved_twh"] < 0.05                            # it closed
    assert "still unserved" not in note                       # so no failure note
    assert "storage tier expandable" not in note              # already checked -> not re-suggested


def test_meet_full_load_reaches_zero_unserved_with_vre_and_storage() -> None:
    # "Meet 100% load" is a hard threshold: closing coal and firming with VRE + storage must reach
    # ZERO unserved. The reservoir sizing grows VRE to the annual energy balance and sizes the long
    # tier to the worst multi-day draw-down, so the store never empties.
    p = load_country_profile("KR")
    caps = dict(p["capacities_gw"])
    caps["coal"] = 0.0  # close coal
    r = calculate_system_lcoe(
        country="KR", shares={}, capacities_gw=caps, carbon_price=0.0, dispatch_mode="data",
        ensemble={"method": "block_bootstrap", "n_samples": 5, "seed": 42, "block_days": 14},
        annual_demand_twh=625.38, expandable=["solar", "wind_onshore", "wind_offshore", "storage"],
        meet_full_load=True, ess_short_power_gw=20.0, ess_short_duration_hr=4.0,
        ess_long_power_gw=5.0, ess_long_duration_hr=168.0,
    )
    added = r["expansion"]["added_capacities_gw"]
    assert r["unserved_twh"] < 0.05                       # HARD threshold: ~zero unserved
    assert any(added.get(k, 0.0) > 0.0 for k in ("solar", "wind_onshore", "wind_offshore"))
    assert added.get("storage_long", 0.0) > 0.0          # a seasonal reservoir bridges the drought
    assert r["ess_requirement_gwh"] > r["ess_short_gwh"]  # most of the energy is the long reservoir


def test_meet_full_load_firm_needs_far_less_storage() -> None:
    # Keeping/growing a firm generator to ride the drought needs vastly less storage than the
    # VRE+storage-only reservoir — the tool should reflect that firm is the cheaper firming path.
    p = load_country_profile("KR")
    caps = dict(p["capacities_gw"])
    caps["coal"] = 0.0
    base = dict(
        country="KR", shares={}, capacities_gw=caps, carbon_price=0.0, dispatch_mode="data",
        ensemble={"method": "block_bootstrap", "n_samples": 5, "seed": 42, "block_days": 14},
        annual_demand_twh=625.38, meet_full_load=True, ess_short_power_gw=20.0,
        ess_short_duration_hr=4.0, ess_long_power_gw=5.0, ess_long_duration_hr=168.0,
    )
    with_gas = calculate_system_lcoe(**base, expandable=["gas_ccgt", "storage"])
    assert with_gas["unserved_twh"] < 0.05                       # firm also meets 100%
    assert with_gas["expansion"]["added_capacities_gw"].get("gas_ccgt", 0.0) > 0.0
    assert with_gas["ess_requirement_gwh"] < 5000.0             # far below the seasonal reservoir


def test_expansion_prefers_firm_over_storage_for_a_lull() -> None:
    # When both a firm generator and storage are expandable, the LDC sizing covers the residual
    # net-load peak with the firm generator (a 4h battery cannot bridge a low-renewable lull), so
    # gas is grown and storage is left alone, and the gap closes cleanly.
    caps = {"solar": 160, "wind_onshore": 70, "gas_ccgt": 18, "coal": 4, "nuclear": 10, "other": 3}
    base = dict(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        ess_short_power_gw=5.0, ess_long_power_gw=2.0,
    )
    with_both = calculate_system_lcoe(**base, expandable=["storage", "gas_ccgt"], meet_full_load=True)
    added = with_both["expansion"]["added_capacities_gw"]

    assert with_both["unserved_twh"] < 0.1                 # firmed to ~100% served
    assert added.get("gas_ccgt", 0.0) > 0.0                # gas firms the drought peak
    assert "storage" not in added                          # firm covers the peak; storage not grown


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
