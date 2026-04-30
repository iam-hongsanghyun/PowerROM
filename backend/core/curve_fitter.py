from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import curve_fit

from backend.core.function_catalog import FUNCTION_CATALOG, FUNCTION_PARAM_ORDER


@dataclass(frozen=True)
class FitResult:
    params: dict[str, float]
    r_squared: float
    confidence_intervals: dict[str, tuple[float, float]]
    sufficient_data: bool
    error_message: str | None = None


def fit_curve(
    data_points: list[tuple[float, float]] | list[list[float]],
    func_type: str,
    bounds: dict[str, list[float]] | None = None,
) -> FitResult:
    if func_type not in FUNCTION_CATALOG:
        raise ValueError(f"Unsupported function type: {func_type}")
    if len(data_points) < 2:
        raise ValueError("At least two data points are required for fitting.")

    x_data = np.array([point[0] for point in data_points], dtype=float)
    y_data = np.array([point[1] for point in data_points], dtype=float)
    param_names = FUNCTION_PARAM_ORDER[func_type]
    model = FUNCTION_CATALOG[func_type]

    fit_kwargs: dict[str, Any] = {"maxfev": 20000}
    if bounds is not None:
        lower = bounds.get("min", [-np.inf] * len(param_names))
        upper = bounds.get("max", [np.inf] * len(param_names))
        fit_kwargs["bounds"] = (lower, upper)

    try:
        popt, pcov = curve_fit(model, x_data, y_data, **fit_kwargs)
        predictions = model(x_data, *popt)
        residual_sum = float(np.sum(np.square(y_data - predictions)))
        total_sum = float(np.sum(np.square(y_data - np.mean(y_data))))
        r_squared = 1.0 if total_sum == 0 else 1.0 - (residual_sum / total_sum)

        param_dict = {name: float(value) for name, value in zip(param_names, popt, strict=True)}
        standard_errors = np.sqrt(np.diag(pcov))
        confidence_intervals = {
            name: (
                float(value - 1.96 * error),
                float(value + 1.96 * error),
            )
            for name, value, error in zip(param_names, popt, standard_errors, strict=True)
        }
        sufficient = len(data_points) >= 5 and r_squared >= 0.85

        return FitResult(
            params=param_dict,
            r_squared=float(r_squared),
            confidence_intervals=confidence_intervals,
            sufficient_data=sufficient,
        )
    except Exception as exc:  # pragma: no cover - scipy error variants are environment-specific
        return FitResult(
            params={name: 0.0 for name in param_names},
            r_squared=0.0,
            confidence_intervals={name: (0.0, 0.0) for name in param_names},
            sufficient_data=False,
            error_message=f"Curve fitting failed: {exc}",
        )
