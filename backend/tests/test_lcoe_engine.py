from backend.core.lcoe_engine import calculate_system_lcoe

_SINGLE = {"method": "single", "n_samples": 1, "sigma": 0.0, "seed": 42}


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


def test_expansion_requires_a_dispatchable() -> None:
    caps = {"solar": 100, "wind_onshore": 50, "gas_ccgt": 10, "coal": 5, "nuclear": 10, "other": 3}
    result = calculate_system_lcoe(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        expandable=["solar", "wind_onshore"], meet_full_load=True,
    )
    # VRE alone cannot firm the peak -> nothing added, explanatory note returned.
    assert not result["expansion"]["added_capacities_gw"]
    assert result["expansion"]["note"]


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
