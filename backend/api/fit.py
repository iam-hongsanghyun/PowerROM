from __future__ import annotations

from fastapi import APIRouter

from backend.core.curve_fitter import fit_curve
from backend.models.schemas import DataQuality, FitRequest, FitResponse

router = APIRouter()


@router.post("/fit", response_model=FitResponse)
def fit(payload: FitRequest) -> FitResponse:
    result = fit_curve(
        data_points=list(payload.data_points),
        func_type=payload.func_type,
        bounds=payload.bounds,
    )
    return FitResponse(
        params=result.params,
        r_squared=result.r_squared,
        confidence_intervals=result.confidence_intervals,
        sufficient_data=result.sufficient_data,
        error_message=result.error_message,
        data_quality=DataQuality(
            notes=["R-squared and 95% confidence intervals from bounded Levenberg-Marquardt least squares (Gauss-Newton covariance)."]
        ),
    )
