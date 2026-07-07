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


def test_expansion_can_grow_storage() -> None:
    # VRE-heavy fleet with a recoverable evening deficit; storage + gas should firm it.
    caps = {"solar": 160, "wind_onshore": 70, "gas_ccgt": 18, "coal": 4, "nuclear": 10, "other": 3}
    base = dict(
        country="KR", shares=caps, carbon_price=50.0, capacities_gw=caps, ensemble=_SINGLE,
        ess_short_power_gw=5.0, ess_long_power_gw=2.0,
    )
    before = calculate_system_lcoe(**base)
    with_storage = calculate_system_lcoe(**base, expandable=["storage"], meet_full_load=True)
    with_both = calculate_system_lcoe(**base, expandable=["storage", "gas_ccgt"], meet_full_load=True)

    # Storage alone narrows the gap and grows the short-duration battery.
    assert with_storage["unserved_twh"] < before["unserved_twh"]
    assert with_storage["expansion"]["added_capacities_gw"].get("storage", 0.0) > 0.0
    # Storage + a dispatchable closes it to ~100% served.
    assert with_both["unserved_twh"] < 0.1
    assert "storage" in with_both["expansion"]["added_capacities_gw"]


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
