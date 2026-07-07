from backend.core.lcoe_engine import calculate_system_lcoe


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
