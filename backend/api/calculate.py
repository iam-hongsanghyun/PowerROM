from __future__ import annotations

from fastapi import APIRouter

from backend.core.lcoe_engine import calculate_system_lcoe
from backend.models.schemas import CalculateRequest, CalculateResponse

router = APIRouter()


@router.post("/calculate", response_model=CalculateResponse)
def calculate(payload: CalculateRequest) -> CalculateResponse:
    result = calculate_system_lcoe(
        country=payload.country,
        shares=payload.shares,
        carbon_price=payload.carbon_price,
        ev_penetration=payload.ev_penetration,
        annual_demand_twh=payload.annual_demand_twh,
        custom_params=payload.custom_params,
        dispatch_mode=payload.dispatch_mode,
        weather_years=payload.weather_years,
        ensemble=payload.ensemble.model_dump() if payload.ensemble else None,
        capacities_gw=payload.capacities_gw,
        generator_order=payload.generator_order,
        min_cf=payload.min_cf,
        max_cf=payload.max_cf,
        ramp_up=payload.ramp_up,
        ramp_down=payload.ramp_down,
        ess_short_power_gw=payload.ess_short_power_gw,
        ess_short_duration_hr=payload.ess_short_duration_hr,
        ess_long_power_gw=payload.ess_long_power_gw,
        ess_long_duration_hr=payload.ess_long_duration_hr,
        demand_pattern=payload.demand_pattern,
        demand_peak_ratio=payload.demand_peak_ratio,
        demand_monthly=payload.demand_monthly,
        demand_daily=payload.demand_daily,
        expandable=payload.expandable,
        meet_full_load=payload.meet_full_load,
        rps_target_share=payload.rps_target_share,
        rps_penalty_usd_mwh=payload.rps_penalty_usd_mwh,
        subsidy_itc_pct=payload.subsidy_itc_pct,
        subsidy_ptc_usd_mwh=payload.subsidy_ptc_usd_mwh,
        fuel_import_tariff_pct=payload.fuel_import_tariff_pct,
    )
    return CalculateResponse(**result)


@router.post("/calculate-batch", response_model=list[CalculateResponse])
def calculate_batch(payloads: list[CalculateRequest]) -> list[CalculateResponse]:
    results = []
    for payload in payloads:
        result = calculate_system_lcoe(
            country=payload.country,
            shares=payload.shares,
            carbon_price=payload.carbon_price,
            ev_penetration=payload.ev_penetration,
            annual_demand_twh=payload.annual_demand_twh,
            custom_params=payload.custom_params,
            dispatch_mode=payload.dispatch_mode,
            weather_years=payload.weather_years,
            ensemble=payload.ensemble.model_dump() if payload.ensemble else None,
            capacities_gw=payload.capacities_gw,
            generator_order=payload.generator_order,
            min_cf=payload.min_cf,
            max_cf=payload.max_cf,
            ramp_up=payload.ramp_up,
            ramp_down=payload.ramp_down,
        )
        results.append(CalculateResponse(**result))
    return results
