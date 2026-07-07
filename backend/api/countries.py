from __future__ import annotations

from backend.core.lcoe_engine import PROFILE_DIR, load_country_profile
from backend.models.schemas import CountriesResponse, CountrySummary, DataQuality
from fastapi import APIRouter

router = APIRouter()


@router.get("/countries", response_model=CountriesResponse)
def countries() -> CountriesResponse:
    items: list[CountrySummary] = []
    for path in sorted(PROFILE_DIR.glob("*.json")):
        profile = load_country_profile(path.stem)
        items.append(
            CountrySummary(
                code=path.stem,
                name=profile["name"],
                annual_generation_twh=profile["annual_generation_twh"],
                annual_demand_twh=profile.get("annual_demand_twh"),
                discount_rate=profile["discount_rate"],
                generators=list(profile["generators"].keys()),
                capacities_gw=profile.get("capacities_gw", {}),
                shares=profile.get("shares", {}),
                data_year=profile.get("data_year"),
                sources=profile.get("sources", []),
            )
        )
    return CountriesResponse(
        countries=items,
        data_quality=DataQuality(
            notes=[
                "Demand, installed capacity and generation mix are per-country actuals from "
                "Ember; technology costs are a shared literature-based template. All fields can "
                "be overridden at request time."
            ]
        ),
    )
