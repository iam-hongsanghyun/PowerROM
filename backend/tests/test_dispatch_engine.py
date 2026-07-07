import numpy as np

from backend.core.dispatch_engine import (
    _size_storage_from_pattern,
    aggregate_dispatch_results,
    dispatch_hourly,
)
from backend.core.hourly_profiles import HOURS_PER_YEAR, YearProfile


def _minimal_profile() -> dict:
    return {
        "generators": {
            "solar": {"cf_base": 0.5},
            "coal": {"cf_base": 1.0},
        }
    }


def test_flat_solar_profile_matches_closed_form() -> None:
    year = YearProfile(
        country="TT",
        year=2024,
        demand_norm=np.ones(HOURS_PER_YEAR),
        solar_cf=np.full(HOURS_PER_YEAR, 0.5),
        wind_cf=np.zeros(HOURS_PER_YEAR),
        source="test",
    )

    result = dispatch_hourly(
        profile=_minimal_profile(),
        year_profile=year,
        shares={"solar": 1.0},
        annual_demand_twh=8.76,
    )
    summary = aggregate_dispatch_results([result], include_ldc=True)

    assert summary["metrics"]["capacity_factor"]["solar"]["median"] == 0.5
    assert summary["metrics"]["energy_twh"]["solar"]["median"] == 8.76
    assert summary["metrics"]["scalars"]["curtailment_rate"]["median"] == 0.0
    assert summary["metrics"]["scalars"]["unserved_twh"]["median"] == 0.0
    np.testing.assert_allclose(result.dispatch_gw["solar"], np.ones(HOURS_PER_YEAR))


def test_variable_solar_conserves_energy_and_marks_curtailment() -> None:
    solar_cf = np.zeros(HOURS_PER_YEAR)
    solar_cf[::2] = 1.0
    year = YearProfile(
        country="TT",
        year=2024,
        demand_norm=np.ones(HOURS_PER_YEAR),
        solar_cf=solar_cf,
        wind_cf=np.zeros(HOURS_PER_YEAR),
        source="test",
    )

    result = dispatch_hourly(
        profile=_minimal_profile(),
        year_profile=year,
        shares={"solar": 1.0},
        annual_demand_twh=8.76,
    )
    summary = aggregate_dispatch_results([result], include_ldc=True)

    served = summary["metrics"]["energy_twh"]["solar"]["median"]
    curtailed = summary["metrics"]["scalars"]["curtailed_twh"]["median"]
    unserved = summary["metrics"]["scalars"]["unserved_twh"]["median"]

    assert served == 4.38
    assert curtailed == 4.38
    assert unserved == 4.38
    assert summary["metrics"]["capacity_factor"]["solar"]["median"] == 0.25
    assert summary["metrics"]["scalars"]["curtailment_rate"]["median"] == 0.5


def test_ldc_sorts_by_gross_load_and_carries_dispatch_hours() -> None:
    demand = np.linspace(0.7, 1.3, HOURS_PER_YEAR)
    solar_cf = np.linspace(0.0, 1.0, HOURS_PER_YEAR)
    year = YearProfile(
        country="TT",
        year=2024,
        demand_norm=demand,
        solar_cf=solar_cf,
        wind_cf=np.zeros(HOURS_PER_YEAR),
        source="test",
    )

    result = dispatch_hourly(
        profile=_minimal_profile(),
        year_profile=year,
        shares={"solar": 0.4, "coal": 0.6},
        annual_demand_twh=8.76,
    )
    summary = aggregate_dispatch_results([result], include_ldc=True)
    sorted_demand = np.asarray(summary["ldc"]["series"]["demand"]["median"])
    sorted_solar = np.asarray(summary["ldc"]["series"]["solar"]["median"])

    assert np.all(np.diff(sorted_demand) <= 0)
    # Solar is not sorted as its own generation-duration curve. It follows the
    # same gross-load hour order, so the highest-load hour appears first.
    assert sorted_solar[0] == result.dispatch_gw["solar"][-1]


def test_generator_order_controls_dispatch_priority() -> None:
    year = YearProfile(
        country="TT",
        year=2024,
        demand_norm=np.ones(HOURS_PER_YEAR),
        solar_cf=np.ones(HOURS_PER_YEAR),
        wind_cf=np.zeros(HOURS_PER_YEAR),
        source="test",
    )

    solar_first = dispatch_hourly(
        profile=_minimal_profile(),
        year_profile=year,
        shares={"solar": 0.5, "coal": 0.5},
        annual_demand_twh=8.76,
        capacities_gw={"solar": 1.0, "coal": 1.0},
        generator_order=["solar", "coal"],
    )
    coal_first = dispatch_hourly(
        profile=_minimal_profile(),
        year_profile=year,
        shares={"solar": 0.5, "coal": 0.5},
        annual_demand_twh=8.76,
        capacities_gw={"solar": 1.0, "coal": 1.0},
        generator_order=["coal", "solar"],
    )

    assert float(np.sum(solar_first.dispatch_gw["solar"])) == HOURS_PER_YEAR
    assert float(np.sum(solar_first.dispatch_gw["coal"])) == 0.0
    assert float(np.sum(coal_first.dispatch_gw["coal"])) == HOURS_PER_YEAR
    assert float(np.sum(coal_first.dispatch_gw["solar"])) == 0.0
    assert float(np.sum(coal_first.curtailed_gw["solar"])) == HOURS_PER_YEAR


def test_storage_sizing_zero_when_no_surplus_or_deficit() -> None:
    zero = np.zeros(HOURS_PER_YEAR)
    result = _size_storage_from_pattern(zero, zero)
    assert all(value == 0.0 for value in result.values())


def test_storage_sizing_intraday_pattern_has_no_seasonal_reservoir() -> None:
    # 1 GW surplus at noon and 1 GW deficit each evening, every day: purely intraday.
    surplus = np.zeros(HOURS_PER_YEAR)
    deficit = np.zeros(HOURS_PER_YEAR)
    surplus[12::24] = 1.0
    deficit[20::24] = 1.0

    result = _size_storage_from_pattern(surplus, deficit)

    # Each day shifts exactly 1 GWh within the day; nothing spills to the next day.
    np.testing.assert_allclose(result["storage_short_shift_gwh"], 1.0, atol=1e-9)
    assert result["storage_long_depth_gwh"] < 1e-9
    assert result["storage_long_recoverable_gwh"] < 1e-9


def test_storage_sizing_seasonal_pattern_fills_reservoir() -> None:
    # Surplus only in the first 90 days, deficit only in the last 90 days: purely seasonal.
    surplus = np.zeros(HOURS_PER_YEAR)
    deficit = np.zeros(HOURS_PER_YEAR)
    surplus[: 90 * 24] = 0.5
    deficit[-90 * 24 :] = 0.5

    result = _size_storage_from_pattern(surplus, deficit)

    # Never surplus and deficit on the same day -> no intraday shifting.
    assert result["storage_short_shift_gwh"] < 1e-9
    # 90 days x 24 h x 0.5 GW = 1080 GWh available on each side, fully recoverable.
    np.testing.assert_allclose(result["storage_long_recoverable_gwh"], 1080.0, rtol=1e-6)
    np.testing.assert_allclose(result["storage_long_depth_gwh"], 1080.0, rtol=1e-6)
