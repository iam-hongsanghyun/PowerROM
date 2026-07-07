from __future__ import annotations

from fastapi import APIRouter

from backend.core.lcoe_engine import calculate_system_lcoe
from backend.models.schemas import CalculateRequest, DispatchResponse

router = APIRouter()


@router.post("/dispatch", response_model=DispatchResponse)
def dispatch(payload: CalculateRequest) -> DispatchResponse:
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
        include_ldc=True,
        capacities_gw=payload.capacities_gw,
        generator_order=payload.generator_order,
    )
    return DispatchResponse(
        country=result["country"],
        shares=result["shares"],
        capacity_shares=result["capacity_shares"],
        capacities_gw=result["capacities_gw"],
        annual_demand_twh=result["annual_demand_twh"],
        dispatch=result["dispatch"],
        ldc=result["ldc"],
        data_quality=result["data_quality"],
    )
