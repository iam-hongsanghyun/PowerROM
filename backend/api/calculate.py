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
        custom_params=payload.custom_params,
    )
    return CalculateResponse(**result)
