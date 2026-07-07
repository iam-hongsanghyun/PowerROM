"""Resource-adequacy metrics from a Monte-Carlo ensemble of hourly dispatch results.

Turns a set of jointly-sampled weather scenarios — each an 8760-hour series of *unserved*
power from :func:`backend.core.dispatch_engine.dispatch_hourly` — into the standard planning
adequacy metrics (LOLE, LOLP, EUE) and, crucially, their **distribution** across scenarios.
The distribution is the point: in a high-renewable system adequacy risk lives in the tail
(multi-day low-renewable droughts), so a p50 that looks fine can sit next to a p99 that does
not. These metrics are only meaningful when the scenarios preserve temporal and cross-variable
dependence — see the coherent block-bootstrap sampler — because independent hourly sampling
factorises the drought probability to ~0 and reports a falsely optimistic LOLE.
"""

from __future__ import annotations

from typing import Any

import numpy as np

HOURS_PER_YEAR = 8760
# Below this a shortfall is floating-point noise, not a loss-of-load hour (GW).
_UNSERVED_TOL_GW = 1e-6


def estimate_adequacy(
    member_unserved_gw: list[np.ndarray],
    annual_demand_twh: float,
    lole_target_hours: float | None = None,
) -> dict[str, Any]:
    r"""Resource-adequacy metrics and their scenario distribution.

    Args:
        member_unserved_gw: One hourly unserved-power array (GW, length 8760) per Monte-Carlo
            scenario — the ``unserved_gw`` field of each ensemble member's dispatch.
        annual_demand_twh: Annual demand (TWh), used to normalise expected unserved energy.
        lole_target_hours: Optional reliability standard (LOLE, hours/year, e.g. 2.4 ≈ "1 day
            in 10 years"). When given, the result reports whether the *expected* LOLE meets it
            and the share of scenarios that individually do.

    Returns:
        Dict with expectation metrics — ``lole_hours`` (LOLE, h/yr), ``lolp`` (hourly loss-of-load
        probability = LOLE/8760), ``loss_of_load_prob_annual`` (share of scenarios with any
        shortfall hour), ``eue_mwh`` (expected unserved energy, MWh/yr) and ``eue_fraction`` (÷
        demand) — plus tail readouts ``unserved_mwh_p50|p90|p95|p99``, ``lole_hours_p50|p95``,
        ``peak_shortfall_gw_p50|p95``, the worst scenario, ``n_scenarios``, and (if a target was
        given) ``lole_target_hours``/``meets_target``/``share_scenarios_meeting_target``.

    Algorithm:
        Per scenario s: loss hours $h_s = \#\{t : u_{s,t} > \epsilon\}$, unserved energy
        $E_s = \sum_t u_{s,t}\,\Delta t$, peak shortfall $\max_t u_{s,t}$.
        $$\mathrm{LOLE} = \tfrac1N\sum_s h_s,\quad \mathrm{LOLP} = \mathrm{LOLE}/8760,\quad
          \mathrm{EUE} = \tfrac1N\sum_s E_s.$$
        ASCII: LOLE = mean_s(loss_hours_s); LOLP = LOLE/8760; EUE = mean_s(unserved_energy_s).
    """
    members = [np.asarray(member, dtype=float) for member in member_unserved_gw]
    if not members:
        raise ValueError("Adequacy needs at least one Monte-Carlo scenario.")

    loss_hours = np.array([int(np.count_nonzero(m > _UNSERVED_TOL_GW)) for m in members], dtype=float)
    unserved_mwh = np.array([float(np.sum(m)) * 1e3 for m in members], dtype=float)  # GWh → MWh
    peak_shortfall_gw = np.array([float(np.max(m)) if m.size else 0.0 for m in members], dtype=float)

    lole_hours = float(np.mean(loss_hours))
    eue_mwh = float(np.mean(unserved_mwh))
    demand_mwh = max(annual_demand_twh, 0.0) * 1e6

    result: dict[str, Any] = {
        "n_scenarios": len(members),
        "lole_hours": lole_hours,
        "lolp": lole_hours / HOURS_PER_YEAR,
        "loss_of_load_prob_annual": float(np.mean(loss_hours > 0.0)),
        "eue_mwh": eue_mwh,
        "eue_fraction": (eue_mwh / demand_mwh) if demand_mwh > 0 else 0.0,
        "unserved_mwh_p50": float(np.percentile(unserved_mwh, 50)),
        "unserved_mwh_p90": float(np.percentile(unserved_mwh, 90)),
        "unserved_mwh_p95": float(np.percentile(unserved_mwh, 95)),
        "unserved_mwh_p99": float(np.percentile(unserved_mwh, 99)),
        "unserved_mwh_max": float(np.max(unserved_mwh)),
        "lole_hours_p50": float(np.percentile(loss_hours, 50)),
        "lole_hours_p95": float(np.percentile(loss_hours, 95)),
        "peak_shortfall_gw_p50": float(np.percentile(peak_shortfall_gw, 50)),
        "peak_shortfall_gw_p95": float(np.percentile(peak_shortfall_gw, 95)),
    }
    if lole_target_hours is not None:
        result["lole_target_hours"] = float(lole_target_hours)
        result["meets_target"] = lole_hours <= float(lole_target_hours)
        result["share_scenarios_meeting_target"] = float(np.mean(loss_hours <= float(lole_target_hours)))
    return result
