from __future__ import annotations

import math
from typing import Callable

import numpy as np


def _linear(x: np.ndarray, a: float, b: float) -> np.ndarray:
    return a + b * x


def _logarithmic(x: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    return a - b * np.log1p(c * x)


def _quadratic(x: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    return a + b * x + c * np.square(x)


def _exponential(x: np.ndarray, a: float, b: float) -> np.ndarray:
    return a * np.exp(b * x)


def _power(x: np.ndarray, a: float, b: float) -> np.ndarray:
    safe_x = np.maximum(x, 0.0)
    return a * np.power(safe_x, b)


def _piecewise(
    x: np.ndarray,
    intercept: float,
    threshold: float,
    slope_before: float,
    slope_after: float,
) -> np.ndarray:
    before = intercept + slope_before * x
    after = intercept + slope_before * threshold + slope_after * (x - threshold)
    return np.where(x <= threshold, before, after)


def _constant(x: np.ndarray, a: float) -> np.ndarray:
    return np.full_like(x, fill_value=a, dtype=float)


FUNCTION_CATALOG: dict[str, Callable[..., np.ndarray]] = {
    "linear": _linear,
    "logarithmic": _logarithmic,
    "quadratic": _quadratic,
    "exponential": _exponential,
    "power": _power,
    "piecewise": _piecewise,
    "constant": _constant,
}

FUNCTION_PARAM_ORDER: dict[str, list[str]] = {
    "linear": ["a", "b"],
    "logarithmic": ["a", "b", "c"],
    "quadratic": ["a", "b", "c"],
    "exponential": ["a", "b"],
    "power": ["a", "b"],
    "piecewise": ["intercept", "threshold", "slope_before", "slope_after"],
    "constant": ["a"],
}


def evaluate_function(
    func_type: str,
    params: dict[str, float],
    x: float | list[float] | np.ndarray,
    x_min: float | None = None,
    x_max: float | None = None,
) -> float | np.ndarray:
    if func_type not in FUNCTION_CATALOG:
        raise ValueError(f"Unsupported function type: {func_type}")

    ordered_names = FUNCTION_PARAM_ORDER[func_type]
    missing = [name for name in ordered_names if name not in params]
    if missing:
        raise ValueError(f"Missing parameters for {func_type}: {', '.join(missing)}")

    x_array = np.asarray(x, dtype=float)
    ordered_values = [float(params[name]) for name in ordered_names]
    result = FUNCTION_CATALOG[func_type](x_array, *ordered_values)

    if x_min is not None or x_max is not None:
        lower = -math.inf if x_min is None else float(x_min)
        upper = math.inf if x_max is None else float(x_max)
        result = np.clip(result, lower, upper)

    if np.isscalar(x) or (isinstance(x, np.ndarray) and x.ndim == 0):
        return float(np.asarray(result).item())
    return result
