"""Least-squares curve fitting for the function catalog — numpy only, no scipy.

The previous implementation delegated to ``scipy.optimize.curve_fit``. scipy's Linux wheel is
~110 MB installed — the single item that pushed the serverless bundle past Vercel's 250 MB
standard limit and into slow on-demand dependency paging (the cold-start latency users saw as
``FUNCTION_INVOCATION_TIMEOUT``). Every catalog model has ≤ 4 parameters, so a hand-rolled
bounded Levenberg–Marquardt with data-informed initialisation replaces it exactly:

* models **linear in their parameters** (linear, quadratic, constant) start from the closed-form
  ordinary-least-squares solution — already optimal, LM then converges in one step;
* nonlinear models start from transformations that linearise them (log-linear for exponential,
  log-log for power, a threshold grid for piecewise, a curvature grid for logarithmic) — a
  *better* start than scipy's default ``p0 = ones``;
* the covariance is the standard Gauss-Newton estimate ``s² (JᵀJ)⁻¹`` with
  ``s² = SSR/(n − p)`` — the same formula scipy reports for ``absolute_sigma=False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from backend.core.function_catalog import FUNCTION_CATALOG, FUNCTION_PARAM_ORDER

_LM_MAX_ITER = 200
_LM_TOL = 1e-12          # relative SSR improvement below which LM stops
_JAC_EPS = 1e-7          # forward-difference step (relative, floored absolutely)


@dataclass(frozen=True)
class FitResult:
    params: dict[str, float]
    r_squared: float
    confidence_intervals: dict[str, tuple[float, float]]
    sufficient_data: bool
    error_message: str | None = None


def _numeric_jacobian(
    model: Callable[..., np.ndarray], x: np.ndarray, p: np.ndarray
) -> np.ndarray:
    """Forward-difference Jacobian J[i, j] = ∂model(x_i)/∂p_j."""
    f0 = np.asarray(model(x, *p), dtype=float)
    jacobian = np.empty((x.size, p.size), dtype=float)
    for j in range(p.size):
        step = _JAC_EPS * max(abs(p[j]), 1.0)
        p_step = p.copy()
        p_step[j] += step
        jacobian[:, j] = (np.asarray(model(x, *p_step), dtype=float) - f0) / step
    return jacobian


def _lm_fit(
    model: Callable[..., np.ndarray],
    x: np.ndarray,
    y: np.ndarray,
    p0: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
) -> tuple[np.ndarray, float]:
    r"""Bounded Levenberg–Marquardt minimisation of the residual sum of squares.

    Algorithm:
        $$p_{k+1} = \mathrm{clip}\!\left(p_k + (J^TJ + \lambda\,\mathrm{diag}(J^TJ))^{-1}
          J^T r,\ \ell,\ u\right)$$
    ASCII: solve (J'J + lambda*diag(J'J)) dp = J'r; accept the clipped step if SSR drops
    (lambda /= 3), else raise lambda (*= 5) and retry. Stop when the relative SSR improvement
    is below tol or lambda overflows.

    ``J`` = numerical Jacobian, ``r = y − model(x, p)``, ``λ`` = damping (Marquardt), bounds
    enforced by projection onto the box ``[ℓ, u]``.

    Returns:
        ``(p, ssr)`` — the best parameters found and their residual sum of squares.
    """
    p = np.clip(p0.astype(float), lower, upper)
    residual = y - np.asarray(model(x, *p), dtype=float)
    ssr = float(residual @ residual)
    lam = 1e-3
    for _ in range(_LM_MAX_ITER):
        jacobian = _numeric_jacobian(model, x, p)
        jtj = jacobian.T @ jacobian
        gradient = jacobian.T @ residual
        improved = False
        for _retry in range(20):
            damped = jtj + lam * np.diag(np.maximum(np.diag(jtj), 1e-12))
            try:
                dp = np.linalg.solve(damped, gradient)
            except np.linalg.LinAlgError:
                lam *= 5.0
                continue
            p_try = np.clip(p + dp, lower, upper)
            residual_try = y - np.asarray(model(x, *p_try), dtype=float)
            ssr_try = float(residual_try @ residual_try)
            if np.isfinite(ssr_try) and ssr_try < ssr:
                relative_gain = (ssr - ssr_try) / max(ssr, 1e-300)
                p, residual, ssr = p_try, residual_try, ssr_try
                lam = max(lam / 3.0, 1e-12)
                improved = True
                if relative_gain < _LM_TOL:
                    return p, ssr
                break
            lam *= 5.0
            if lam > 1e12:
                return p, ssr
        if not improved:
            return p, ssr
    return p, ssr


def _polyfit_init(x: np.ndarray, y: np.ndarray, degree: int) -> np.ndarray:
    """Closed-form OLS for models linear in their parameters (a + b·x + …)."""
    design = np.vander(x, degree + 1, increasing=True)
    coeffs, *_ = np.linalg.lstsq(design, y, rcond=None)
    return coeffs


def _initial_guesses(func_type: str, x: np.ndarray, y: np.ndarray) -> list[np.ndarray]:
    """Deterministic, data-informed starting points (best first, ``ones`` as backstop)."""
    guesses: list[np.ndarray] = []
    if func_type == "constant":
        guesses.append(np.array([float(np.mean(y))]))
    elif func_type == "linear":
        guesses.append(_polyfit_init(x, y, 1))
    elif func_type == "quadratic":
        guesses.append(_polyfit_init(x, y, 2))
    elif func_type == "exponential":
        # log-linearise y = a·e^{bx} where possible (needs y of one sign and ≥2 distinct x)
        if np.all(y > 0) and np.unique(x).size >= 2:
            slope, intercept = np.polyfit(x, np.log(y), 1)
            guesses.append(np.array([float(np.exp(intercept)), float(slope)]))
    elif func_type == "power":
        # log-log linearise y = a·x^b on the strictly positive points
        mask = (x > 0) & (y > 0)
        if np.unique(x[mask]).size >= 2:
            slope, intercept = np.polyfit(np.log(x[mask]), np.log(y[mask]), 1)
            guesses.append(np.array([float(np.exp(intercept)), float(slope)]))
    elif func_type == "logarithmic":
        # y = a − b·log1p(c·x): for each c on a log grid, (a, b) is a linear subproblem.
        best: tuple[float, np.ndarray] | None = None
        for c in np.geomspace(1e-3, 1e3, 13):
            design = np.column_stack([np.ones_like(x), -np.log1p(c * x)])
            (a, b), *_ = np.linalg.lstsq(design, y, rcond=None)
            ssr = float(np.sum((y - (a - b * np.log1p(c * x))) ** 2))
            if best is None or ssr < best[0]:
                best = (ssr, np.array([a, b, c]))
        if best is not None:
            guesses.append(best[1])
    elif func_type == "piecewise":
        # For each candidate threshold (interior x quantiles), both slopes and the intercept
        # form a linear subproblem; keep the best.
        model = FUNCTION_CATALOG["piecewise"]
        best = None
        for threshold in np.unique(np.quantile(x, np.linspace(0.15, 0.85, 8))):
            before = np.minimum(x, threshold)
            after = np.maximum(x - threshold, 0.0)
            design = np.column_stack([np.ones_like(x), before, after])
            (intercept, slope_before, slope_after), *_ = np.linalg.lstsq(design, y, rcond=None)
            p = np.array([intercept, threshold, slope_before, slope_after])
            ssr = float(np.sum((y - model(x, *p)) ** 2))
            if best is None or ssr < best[0]:
                best = (ssr, p)
        if best is not None:
            guesses.append(best[1])
    guesses.append(np.ones(len(FUNCTION_PARAM_ORDER[func_type])))
    return guesses


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
    n_params = len(param_names)

    lower = np.full(n_params, -np.inf)
    upper = np.full(n_params, np.inf)
    if bounds is not None:
        lower = np.asarray(bounds.get("min", lower), dtype=float)
        upper = np.asarray(bounds.get("max", upper), dtype=float)

    try:
        best: tuple[float, np.ndarray] | None = None
        for p0 in _initial_guesses(func_type, x_data, y_data):
            popt, ssr = _lm_fit(model, x_data, y_data, p0, lower, upper)
            if not np.all(np.isfinite(popt)):
                continue
            if best is None or ssr < best[0]:
                best = (ssr, popt)
        if best is None:
            raise RuntimeError("No starting point produced a finite fit.")
        residual_sum, popt = best

        total_sum = float(np.sum(np.square(y_data - np.mean(y_data))))
        r_squared = 1.0 if total_sum == 0 else 1.0 - (residual_sum / total_sum)

        # Gauss-Newton covariance s²(JᵀJ)⁻¹, s² = SSR/(n−p) — scipy's absolute_sigma=False
        # convention. Degenerate systems (n ≤ p, singular JᵀJ) report infinite uncertainty.
        dof = x_data.size - n_params
        if dof > 0:
            jacobian = _numeric_jacobian(model, x_data, popt)
            try:
                pcov = np.linalg.inv(jacobian.T @ jacobian) * (residual_sum / dof)
            except np.linalg.LinAlgError:
                pcov = np.full((n_params, n_params), np.inf)
        else:
            pcov = np.full((n_params, n_params), np.inf)
        standard_errors = np.sqrt(np.maximum(np.diag(pcov), 0.0))

        param_dict = {name: float(value) for name, value in zip(param_names, popt, strict=True)}
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
    except Exception as exc:  # noqa: BLE001 — degenerate data must fail gracefully, not 500
        return FitResult(
            params={name: 0.0 for name in param_names},
            r_squared=0.0,
            confidence_intervals={name: (0.0, 0.0) for name in param_names},
            sufficient_data=False,
            error_message=f"Curve fitting failed: {exc}",
        )
