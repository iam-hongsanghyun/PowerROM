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
        )
        results.append(CalculateResponse(**result))
    return results
