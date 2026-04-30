import math

from backend.core.curve_fitter import fit_curve


def test_fit_linear_curve() -> None:
    data_points = [(0.0, 1.0), (1.0, 3.0), (2.0, 5.0), (3.0, 7.0), (4.0, 9.0)]
    result = fit_curve(data_points, "linear")

    assert math.isclose(result.params["a"], 1.0, rel_tol=1e-3)
    assert math.isclose(result.params["b"], 2.0, rel_tol=1e-3)
    assert result.r_squared > 0.999
    assert result.sufficient_data is True


def test_fit_quadratic_curve() -> None:
    data_points = [(0.0, 2.0), (1.0, 5.0), (2.0, 10.0), (3.0, 17.0), (4.0, 26.0)]
    result = fit_curve(data_points, "quadratic")

    assert math.isclose(result.params["a"], 2.0, rel_tol=1e-3)
    assert math.isclose(result.params["b"], 2.0, rel_tol=1e-3)
    assert math.isclose(result.params["c"], 1.0, rel_tol=1e-3)


def test_fit_failure_gracefully_returns_error() -> None:
    data_points = [(0.0, 1.0), (0.0, 2.0)]
    result = fit_curve(data_points, "exponential")

    assert result.sufficient_data is False
    assert result.r_squared <= 0.0
