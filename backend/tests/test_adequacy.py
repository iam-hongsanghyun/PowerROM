import numpy as np
import pytest

from backend.core.adequacy import HOURS_PER_YEAR, estimate_adequacy


def _year(*, loss_hours: int = 0, gw: float = 0.0) -> np.ndarray:
    """An 8760-hour unserved series: ``gw`` GW short for the first ``loss_hours`` hours."""
    series = np.zeros(HOURS_PER_YEAR, dtype=float)
    series[:loss_hours] = gw
    return series


def test_perfectly_reliable_system_has_zero_adequacy_risk() -> None:
    result = estimate_adequacy([_year(), _year()], annual_demand_twh=500.0)
    assert result["lole_hours"] == 0.0
    assert result["lolp"] == 0.0
    assert result["eue_mwh"] == 0.0
    assert result["loss_of_load_prob_annual"] == 0.0


def test_lole_eue_are_scenario_expectations() -> None:
    # One scenario loses 10 h at 1 GW (= 10 GWh = 10_000 MWh), one loses nothing.
    members = [_year(loss_hours=10, gw=1.0), _year()]
    result = estimate_adequacy(members, annual_demand_twh=500.0)

    assert result["lole_hours"] == pytest.approx(5.0)                 # mean(10, 0)
    assert result["lolp"] == pytest.approx(5.0 / HOURS_PER_YEAR)
    assert result["eue_mwh"] == pytest.approx(5_000.0)               # mean(10_000, 0)
    assert result["loss_of_load_prob_annual"] == pytest.approx(0.5)  # 1 of 2 scenarios short
    assert result["eue_fraction"] == pytest.approx(5_000.0 / (500.0 * 1e6))


def test_tail_exceeds_median_when_risk_is_concentrated() -> None:
    # 99 clean years and one bad year: the mean/tail must reflect the rare event, not the median.
    members = [_year() for _ in range(99)] + [_year(loss_hours=50, gw=2.0)]
    result = estimate_adequacy(members, annual_demand_twh=500.0)

    assert result["unserved_mwh_p50"] == 0.0                 # typical year is fine
    assert result["unserved_mwh_max"] == pytest.approx(100_000.0)  # 50 h × 2 GW = 100 GWh
    assert result["unserved_mwh_p99"] > result["unserved_mwh_p50"]  # the tail is where the risk is
    assert result["lole_hours"] == pytest.approx(0.5)        # 50 h over 100 scenarios


def test_target_reporting() -> None:
    members = [_year(loss_hours=1, gw=1.0), _year(loss_hours=5, gw=1.0)]  # LOLE = mean(1, 5) = 3 h
    result = estimate_adequacy(members, annual_demand_twh=500.0, lole_target_hours=2.4)

    assert result["lole_hours"] == pytest.approx(3.0)
    assert result["meets_target"] is False                    # 3 h > 2.4 h standard
    assert result["share_scenarios_meeting_target"] == pytest.approx(0.5)  # only the 1-h scenario


def test_empty_ensemble_raises() -> None:
    with pytest.raises(ValueError):
        estimate_adequacy([], annual_demand_twh=500.0)
