import numpy as np
import pytest
from pydantic import ValidationError

from backend.core.dispatch_engine import (
    _simulate_storage_soc,
    aggregate_dispatch_results,
    dispatch_hourly,
)
from backend.core.hourly_profiles import HOURS_PER_YEAR, YearProfile
from backend.models.schemas import CalculateRequest


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


def _thermal_profile() -> dict:
    # Coal is cheaper on fuel but dirtier; gas is pricier on fuel but lower-carbon.
    # Marginal-cost crossover (coal 30+0.9c vs gas 70+0.4c) is at carbon = 80 $/tCO2.
    return {
        "generators": {
            "coal": {"cf_base": 1.0, "heat_rate_mmbtu_mwh": 10.0, "fuel_usd_mmbtu": 3.0, "emission_factor_tco2_mwh": 0.9},
            "gas_ccgt": {"cf_base": 1.0, "heat_rate_mmbtu_mwh": 7.0, "fuel_usd_mmbtu": 10.0, "emission_factor_tco2_mwh": 0.4},
        }
    }


def _flat_year() -> YearProfile:
    return YearProfile(
        country="TT",
        year=2024,
        demand_norm=np.ones(HOURS_PER_YEAR),
        solar_cf=np.ones(HOURS_PER_YEAR),
        wind_cf=np.zeros(HOURS_PER_YEAR),
        source="test",
    )


def test_vre_is_priority_regardless_of_manual_order() -> None:
    # Even with coal placed first in the manual order, free solar is served first.
    year = _flat_year()
    result = dispatch_hourly(
        profile={"generators": {"solar": {"cf_base": 1.0}, "coal": {"cf_base": 1.0}}},
        year_profile=year,
        shares={"solar": 0.5, "coal": 0.5},
        annual_demand_twh=8.76,
        capacities_gw={"solar": 1.0, "coal": 1.0},
        generator_order=["coal", "solar"],
    )
    # Flat demand 1 GW, solar available 1 GW → solar covers all, coal never runs.
    assert float(np.sum(result.dispatch_gw["solar"])) == HOURS_PER_YEAR
    assert float(np.sum(result.dispatch_gw["coal"])) == 0.0


def test_carbon_price_reorders_the_merit_stack() -> None:
    # Demand needs only one of the two thermals; the cheaper (by marginal cost) runs.
    year = YearProfile(
        country="TT", year=2024,
        demand_norm=np.ones(HOURS_PER_YEAR),
        solar_cf=np.zeros(HOURS_PER_YEAR),
        wind_cf=np.zeros(HOURS_PER_YEAR),
        source="test",
    )
    kwargs = dict(
        profile=_thermal_profile(), year_profile=year,
        shares={"coal": 0.5, "gas_ccgt": 0.5}, annual_demand_twh=8.76,
        capacities_gw={"coal": 1.0, "gas_ccgt": 1.0},
    )
    low = dispatch_hourly(carbon_price=0.0, **kwargs)    # coal cheaper
    high = dispatch_hourly(carbon_price=200.0, **kwargs)  # gas cheaper

    assert float(np.sum(low.dispatch_gw["coal"])) == HOURS_PER_YEAR
    assert float(np.sum(low.dispatch_gw["gas_ccgt"])) == 0.0
    assert float(np.sum(high.dispatch_gw["gas_ccgt"])) == HOURS_PER_YEAR
    assert float(np.sum(high.dispatch_gw["coal"])) == 0.0


def test_nuclear_must_run_displaces_free_vre() -> None:
    # Nuclear runs at its base CF as must-run baseload; abundant free solar curtails
    # around it rather than pushing it off.
    year = _flat_year()  # solar_cf = 1.0, demand = 1.0
    result = dispatch_hourly(
        profile={"generators": {"nuclear": {"cf_base": 0.8}, "solar": {"cf_base": 1.0}}},
        year_profile=year,
        shares={"nuclear": 0.5, "solar": 0.5},
        annual_demand_twh=8.76,
        capacities_gw={"nuclear": 1.0, "solar": 5.0},
    )
    # Nuclear runs flat at 0.8 GW; solar serves the residual 0.2 GW and curtails the rest.
    np.testing.assert_allclose(result.dispatch_gw["nuclear"], np.full(HOURS_PER_YEAR, 0.8))
    np.testing.assert_allclose(result.dispatch_gw["solar"], np.full(HOURS_PER_YEAR, 0.2))
    assert float(np.sum(result.curtailed_gw["solar"])) > 0.0
    assert float(np.sum(result.unserved_gw)) == 0.0


def test_max_cf_caps_thermal_availability() -> None:
    # A hard availability ceiling: coal may never dispatch above capacity × max_cf. Demand
    # (8 GW flat) exceeds the 5 GW ceiling on a 10 GW fleet, so 3 GW/h goes unserved.
    year = _flat_year()  # demand = 1.0 flat
    kwargs = dict(
        profile={"generators": {"coal": {"cf_base": 1.0}}},
        year_profile=year,
        shares={"coal": 1.0},
        annual_demand_twh=8.0 * HOURS_PER_YEAR / 1000,  # 8 GW flat
        capacities_gw={"coal": 10.0},
    )
    base = dispatch_hourly(**kwargs)
    capped = dispatch_hourly(**kwargs, max_cf={"coal": 0.5})
    assert float(np.max(base.dispatch_gw["coal"])) == 8.0  # unconstrained serves the load
    np.testing.assert_allclose(capped.dispatch_gw["coal"], np.full(HOURS_PER_YEAR, 5.0))
    np.testing.assert_allclose(capped.unserved_gw, np.full(HOURS_PER_YEAR, 3.0))


def test_min_cf_forces_must_run_floor_and_spills() -> None:
    # A must-run floor: coal runs at least capacity × min_cf every hour even when demand (3 GW)
    # is below the 5 GW floor on a 10 GW fleet — the 2 GW/h surplus is spilled (curtailed).
    year = _flat_year()
    kwargs = dict(
        profile={"generators": {"coal": {"cf_base": 1.0}}},
        year_profile=year,
        shares={"coal": 1.0},
        annual_demand_twh=3.0 * HOURS_PER_YEAR / 1000,  # 3 GW flat
        capacities_gw={"coal": 10.0},
    )
    base = dispatch_hourly(**kwargs)
    floored = dispatch_hourly(**kwargs, min_cf={"coal": 0.5})
    np.testing.assert_allclose(base.dispatch_gw["coal"], np.full(HOURS_PER_YEAR, 3.0))  # merit only
    assert float(np.sum(base.curtailed_gw["coal"])) == 0.0
    np.testing.assert_allclose(floored.dispatch_gw["coal"], np.full(HOURS_PER_YEAR, 5.0))  # floor binds
    np.testing.assert_allclose(floored.curtailed_gw["coal"], np.full(HOURS_PER_YEAR, 2.0))  # spilled


def test_cf_limits_validation_rejects_min_above_max() -> None:
    common = dict(country="KR", shares={"coal": 1.0}, carbon_price=50.0)
    with pytest.raises(ValidationError):
        CalculateRequest(**common, min_cf={"coal": 0.9}, max_cf={"coal": 0.4})
    with pytest.raises(ValidationError):
        CalculateRequest(**common, max_cf={"coal": 1.5})  # out of [0, 1]
    # A valid, consistent pair is accepted.
    CalculateRequest(**common, min_cf={"coal": 0.3}, max_cf={"coal": 0.8})


def test_ramp_limits_bound_hourly_output_change() -> None:
    # An alternating high/low demand forces a large hourly swing. A 10%/h ramp cap on coal (a 10 GW
    # fleet ⇒ 1 GW/h) means it cannot follow: its hour-to-hour change is bounded and the unfollowable
    # swing shows up as extra curtailment + unserved that the unconstrained merit dispatch never had.
    alt = np.resize(np.array([1.5, 0.5]), HOURS_PER_YEAR)  # mean 1.0, swings ±0.5 every hour
    year = YearProfile(
        country="TT", year=2024, demand_norm=alt,
        solar_cf=np.zeros(HOURS_PER_YEAR), wind_cf=np.zeros(HOURS_PER_YEAR), source="test",
    )
    kwargs = dict(
        profile={"generators": {"coal": {"cf_base": 1.0}}}, year_profile=year,
        shares={"coal": 1.0}, annual_demand_twh=5.0 * HOURS_PER_YEAR / 1000,  # mean 5 GW
        capacities_gw={"coal": 10.0},
    )
    base = dispatch_hourly(**kwargs)
    ramped = dispatch_hourly(**kwargs, ramp_up={"coal": 0.1}, ramp_down={"coal": 0.1})
    base_swing = float(np.max(np.abs(np.diff(base.dispatch_gw["coal"]))))
    ramp_swing = float(np.max(np.abs(np.diff(ramped.dispatch_gw["coal"]))))
    assert base_swing > 4.0                    # unconstrained coal chases the full ~5 GW swing
    assert ramp_swing <= 0.1 * 10.0 + 1e-6     # ramp caps the hourly change at 1 GW
    base_mismatch = float(np.sum(base.curtailed_gw["coal"]) + np.sum(base.unserved_gw))
    ramp_mismatch = float(np.sum(ramped.curtailed_gw["coal"]) + np.sum(ramped.unserved_gw))
    assert base_mismatch < 1.0 and ramp_mismatch > base_mismatch + 1.0  # ramp forces a mismatch


def test_profile_ramp_default_binds_without_explicit_arg() -> None:
    # Config-backed default: a ramp rate carried in the profile's generator block (as the real
    # country profiles now ship) binds even with no ramp argument passed; an explicit arg overrides it.
    alt = np.resize(np.array([1.5, 0.5]), HOURS_PER_YEAR)
    year = YearProfile(
        country="TT", year=2024, demand_norm=alt,
        solar_cf=np.zeros(HOURS_PER_YEAR), wind_cf=np.zeros(HOURS_PER_YEAR), source="test",
    )
    profile = {"generators": {"coal": {
        "cf_base": 1.0, "ramp_up_frac_per_hr": 0.1, "ramp_down_frac_per_hr": 0.1,
    }}}
    kwargs = dict(
        profile=profile, year_profile=year, shares={"coal": 1.0},
        annual_demand_twh=5.0 * HOURS_PER_YEAR / 1000, capacities_gw={"coal": 10.0},
    )
    default = dispatch_hourly(**kwargs)  # no ramp_up / ramp_down argument -> profile default applies
    swing = float(np.max(np.abs(np.diff(default.dispatch_gw["coal"]))))
    assert swing <= 0.1 * 10.0 + 1e-6  # the profile's 10 %/h default bounds the hourly change
    loose = dispatch_hourly(**kwargs, ramp_up={"coal": 1.0}, ramp_down={"coal": 1.0})  # arg overrides
    assert float(np.max(np.abs(np.diff(loose.dispatch_gw["coal"])))) > swing


def test_ramp_limits_absent_is_a_noop() -> None:
    # No ramp rates ⇒ the fast vectorized fill is kept and results are byte-identical (regression).
    year = _flat_year()
    kwargs = dict(
        profile={"generators": {"solar": {"cf_base": 1.0}, "coal": {"cf_base": 1.0}}},
        year_profile=year, shares={"solar": 0.5, "coal": 0.5},
        annual_demand_twh=8.76, capacities_gw={"solar": 1.0, "coal": 1.0},
    )
    base = dispatch_hourly(**kwargs)
    same = dispatch_hourly(**kwargs, ramp_up={}, ramp_down={})
    for gen in ("solar", "coal"):
        np.testing.assert_array_equal(base.dispatch_gw[gen], same.dispatch_gw[gen])


def test_ramp_validation_rejects_negative_rate() -> None:
    common = dict(country="KR", shares={"coal": 1.0}, carbon_price=50.0)
    with pytest.raises(ValidationError):
        CalculateRequest(**common, ramp_up={"coal": -0.1})
    CalculateRequest(**common, ramp_up={"coal": 0.3}, ramp_down={"coal": 0.5})  # valid


def test_min_max_cf_absent_is_a_noop() -> None:
    # Empty/None limits must dispatch identically to the unconstrained call (backward compatible).
    year = _flat_year()
    kwargs = dict(
        profile={"generators": {"solar": {"cf_base": 1.0}, "coal": {"cf_base": 1.0}}},
        year_profile=year,
        shares={"solar": 0.5, "coal": 0.5},
        annual_demand_twh=8.76,
        capacities_gw={"solar": 1.0, "coal": 1.0},
    )
    base = dispatch_hourly(**kwargs)
    same = dispatch_hourly(**kwargs, min_cf={}, max_cf={})
    for gen in ("solar", "coal"):
        np.testing.assert_array_equal(base.dispatch_gw[gen], same.dispatch_gw[gen])


def test_storage_soc_shifts_energy_within_limits() -> None:
    # 1 GW surplus for 10 h, then 1 GW deficit for 10 h; a 1 GW / 5 GWh store, lossless.
    surplus = np.zeros(20)
    deficit = np.zeros(20)
    surplus[:10] = 1.0
    deficit[10:] = 1.0
    charge, discharge = _simulate_storage_soc(surplus, deficit, power_gw=1.0, energy_gwh=5.0, efficiency=1.0)
    # Fills 5 GWh (energy-limited), then returns all 5 GWh to the deficit.
    np.testing.assert_allclose(charge.sum(), 5.0)
    np.testing.assert_allclose(discharge.sum(), 5.0)
    assert charge[5:10].sum() == 0.0  # full after 5 h
    assert discharge[15:].sum() == 0.0  # empty after serving 5 h


def test_storage_soc_applies_round_trip_efficiency() -> None:
    surplus = np.zeros(20)
    deficit = np.zeros(20)
    surplus[:10] = 1.0
    deficit[10:] = 1.0
    charge, discharge = _simulate_storage_soc(surplus, deficit, power_gw=1.0, energy_gwh=5.0, efficiency=0.5)
    # Stores 5 GWh but only delivers 50% of it.
    np.testing.assert_allclose(charge.sum(), 5.0)
    np.testing.assert_allclose(discharge.sum(), 2.5)


def test_storage_in_dispatch_reduces_curtailment_and_unserved() -> None:
    # Solar on even hours (2 GW available), demand flat 1 GW: even hours curtail 1 GW,
    # odd hours unserved 1 GW. A 1 GW / long-duration store should mop up both.
    solar_cf = np.zeros(HOURS_PER_YEAR)
    solar_cf[::2] = 1.0
    year = YearProfile("TT", 2024, np.ones(HOURS_PER_YEAR), solar_cf, np.zeros(HOURS_PER_YEAR), "test")
    kwargs = dict(
        profile={"generators": {"solar": {"cf_base": 0.5}}},
        year_profile=year, shares={"solar": 1.0}, annual_demand_twh=8.76,
        capacities_gw={"solar": 2.0},
    )
    without = dispatch_hourly(**kwargs)
    with_storage = dispatch_hourly(
        **kwargs,
        storage_tiers=[{"name": "short", "power_gw": 1.0, "duration_hr": 8.0, "efficiency": 1.0}],
    )
    assert float(np.sum(without.curtailed_gw["solar"])) > 0.0
    assert float(np.sum(without.unserved_gw)) > 0.0
    assert float(np.sum(with_storage.curtailed_gw["solar"])) < float(np.sum(without.curtailed_gw["solar"]))
    assert float(np.sum(with_storage.unserved_gw)) < float(np.sum(without.unserved_gw))


def test_ldc_carries_storage_discharge_and_charge() -> None:
    # The load-duration curve must expose a storage series so the chart can stack discharge and
    # draw charge below zero, like the chronological view.
    solar_cf = np.zeros(HOURS_PER_YEAR)
    solar_cf[::2] = 1.0
    year = YearProfile("TT", 2024, np.ones(HOURS_PER_YEAR), solar_cf, np.zeros(HOURS_PER_YEAR), "test")
    result = dispatch_hourly(
        profile={"generators": {"solar": {"cf_base": 0.5}}},
        year_profile=year, shares={"solar": 1.0}, annual_demand_twh=8.76,
        capacities_gw={"solar": 2.0},
        storage_tiers=[{"name": "short", "power_gw": 1.0, "duration_hr": 8.0, "efficiency": 1.0}],
    )
    summary = aggregate_dispatch_results([result], include_ldc=True)
    storage = np.asarray(summary["ldc"]["series"]["storage"]["median"])

    assert "storage" in summary["ldc"]["series"]
    assert storage.max() > 0.0   # discharge (serves load) — stacks on generation
    assert storage.min() < 0.0   # charge (absorbs surplus) — drawn below zero
