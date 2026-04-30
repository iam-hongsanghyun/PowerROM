from __future__ import annotations

from fastapi import APIRouter

from backend.core.completeness_checker import validate_generator_config
from backend.models.schemas import DataQuality, ValidateRequest, ValidateResponse

router = APIRouter()


@router.post("/validate", response_model=ValidateResponse)
def validate(payload: ValidateRequest) -> ValidateResponse:
    result = validate_generator_config(payload.generator_config)
    return ValidateResponse(
        **result,
        data_quality=DataQuality(notes=["Validation reports fitted, default, and missing fields."]),
    )
