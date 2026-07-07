from __future__ import annotations

from fastapi import APIRouter

from backend.core.lcoe_engine import simulate_pathway
from backend.models.schemas import PathwayRequest, PathwayResponse

router = APIRouter()


@router.post("/pathway", response_model=PathwayResponse)
def pathway(payload: PathwayRequest) -> PathwayResponse:
    result = simulate_pathway(
        country=payload.country,
        start_capacities=payload.start_capacities_gw,
        target_capacities=payload.target_capacities_gw,
        years=sorted(payload.years),
        carbon_price_start=payload.carbon_price_start,
        carbon_price_end=payload.carbon_price_end,
        annual_demand_twh_start=payload.annual_demand_twh_start,
        annual_demand_twh_end=payload.annual_demand_twh_end,
        ensemble=payload.ensemble.model_dump() if payload.ensemble else None,
        ess_short_power_gw=payload.ess_short_power_gw,
        ess_short_duration_hr=payload.ess_short_duration_hr,
        ess_long_power_gw=payload.ess_long_power_gw,
        ess_long_duration_hr=payload.ess_long_duration_hr,
    )
    return PathwayResponse(**result)
