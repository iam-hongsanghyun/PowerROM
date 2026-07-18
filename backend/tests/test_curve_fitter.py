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


# ── Analytical checks for the scipy-free bounded Levenberg–Marquardt fitter ──────

def test_fit_exponential_exact() -> None:
    a, b = 2.0, 0.5
    data_points = [(x, a * math.exp(b * x)) for x in [0.0, 1.0, 2.0, 3.0, 4.0]]
    result = fit_curve(data_points, "exponential")

    assert math.isclose(result.params["a"], a, rel_tol=1e-4)
    assert math.isclose(result.params["b"], b, rel_tol=1e-4)
    assert result.r_squared > 0.9999


def test_fit_power_exact() -> None:
    a, b = 3.0, 1.7
    data_points = [(x, a * x**b) for x in [1.0, 2.0, 3.0, 4.0, 5.0]]
    result = fit_curve(data_points, "power")

    assert math.isclose(result.params["a"], a, rel_tol=1e-4)
    assert math.isclose(result.params["b"], b, rel_tol=1e-4)


def test_fit_logarithmic_exact() -> None:
    a, b, c = 5.0, 1.2, 0.8
    data_points = [(x, a - b * math.log1p(c * x)) for x in [0.0, 0.5, 1.0, 2.0, 4.0, 8.0]]
    result = fit_curve(data_points, "logarithmic")

    predictions_close = all(
        math.isclose(
            result.params["a"] - result.params["b"] * math.log1p(result.params["c"] * x),
            y, rel_tol=1e-3, abs_tol=1e-3,
        )
        for x, y in data_points
    )
    assert predictions_close
    assert result.r_squared > 0.9999


def test_fit_piecewise_exact() -> None:
    intercept, threshold, s1, s2 = 1.0, 3.0, 2.0, -0.5
    def y(x: float) -> float:
        return intercept + s1 * min(x, threshold) + s2 * max(x - threshold, 0.0)
    data_points = [(x, y(x)) for x in [0.0, 1.0, 2.0, 2.5, 3.5, 4.0, 5.0, 6.0]]
    result = fit_curve(data_points, "piecewise")

    assert math.isclose(result.params["intercept"], intercept, rel_tol=1e-3, abs_tol=1e-3)
    assert math.isclose(result.params["threshold"], threshold, rel_tol=5e-2)
    assert math.isclose(result.params["slope_before"], s1, rel_tol=1e-2)
    assert math.isclose(result.params["slope_after"], s2, rel_tol=1e-2)
    assert result.r_squared > 0.999


def test_fit_respects_bounds() -> None:
    data_points = [(0.0, 1.0), (1.0, 3.0), (2.0, 5.0), (3.0, 7.0), (4.0, 9.0)]
    result = fit_curve(data_points, "linear", bounds={"min": [0.0, 0.0], "max": [10.0, 1.5]})

    assert result.params["b"] <= 1.5 + 1e-9  # true slope 2.0 is outside the box
    assert result.params["b"] > 1.4          # solver pushes to the binding bound


def test_confidence_interval_covers_truth_on_noiseless_fit() -> None:
    data_points = [(0.0, 1.0), (1.0, 3.0), (2.0, 5.0), (3.0, 7.0), (4.0, 9.0)]
    result = fit_curve(data_points, "linear")

    lo, hi = result.confidence_intervals["b"]
    assert lo <= 2.0 <= hi
    assert hi - lo < 1e-3  # noiseless data → near-zero parameter uncertainty
