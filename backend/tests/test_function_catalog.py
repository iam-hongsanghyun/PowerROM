import math

import numpy as np
import pytest

from backend.core.function_catalog import evaluate_function


def test_linear_function() -> None:
    assert evaluate_function("linear", {"a": 1, "b": 2}, 3) == 7


def test_logarithmic_function() -> None:
    value = evaluate_function("logarithmic", {"a": 5, "b": 2, "c": 1}, 1)
    assert math.isclose(value, 5 - 2 * math.log(2), rel_tol=1e-9)


def test_quadratic_function() -> None:
    assert evaluate_function("quadratic", {"a": 1, "b": 2, "c": 3}, 2) == 17


def test_exponential_function() -> None:
    value = evaluate_function("exponential", {"a": 2, "b": 0.5}, 2)
    assert math.isclose(value, 2 * math.exp(1), rel_tol=1e-9)


def test_power_function() -> None:
    assert evaluate_function("power", {"a": 3, "b": 2}, 4) == 48


def test_piecewise_function() -> None:
    below = evaluate_function(
        "piecewise",
        {"intercept": 1, "threshold": 0.4, "slope_before": 2, "slope_after": 5},
        0.2,
    )
    above = evaluate_function(
        "piecewise",
        {"intercept": 1, "threshold": 0.4, "slope_before": 2, "slope_after": 5},
        0.6,
    )
    assert math.isclose(below, 1.4, rel_tol=1e-9)
    assert math.isclose(above, 2.8, rel_tol=1e-9)


def test_constant_function() -> None:
    assert evaluate_function("constant", {"a": 0.75}, 999) == 0.75


def test_output_clamping() -> None:
    assert evaluate_function("linear", {"a": 1, "b": -5}, 1, x_min=0.2, x_max=1.0) == 0.2


def test_array_input() -> None:
    values = evaluate_function("linear", {"a": 1, "b": 1}, np.array([0.0, 1.0, 2.0]))
    assert np.allclose(values, np.array([1.0, 2.0, 3.0]))


def test_unknown_function_raises() -> None:
    with pytest.raises(ValueError):
        evaluate_function("unknown", {"a": 1}, 0)
