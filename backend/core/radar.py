"""System Radar: six 0–100 policy-trilemma axes computed from one engine result.

Where the WEC Energy Trilemma Index scores country-years from national statistics, this radar
scores *scenarios*: every axis reads a number the LCOE/dispatch engine already produced for the
exact mix, storage and policy levers on screen, so moving a lever reshapes the polygon.

Axes and anchors (every score decomposes to a sourced physical number):

* **affordability** — system LCOE ($/MWh), scored as the complement of its empirical-CDF
  position within the all-country baseline LCOE distribution (``radar_benchmarks.json``,
  built by ``backend.data.build_radar_benchmarks``). Cheapest observed ≈ 100, priciest ≈ 0.
* **price_stability** — fuel share of system LCOE. Fuel is the volatile, market-priced
  component (IEA/EIA fuel-price indices move multiples in a year; capex and O&M are contracted),
  so exposure is scored linearly: 0 % fuel → 100, 100 % fuel → 0.
* **reliability** — LOLE (h/yr) against the classic "1 day in 10 years" planning standard
  (2.4 h/yr, e.g. NERC/MISO practice): meeting it exactly scores 75, and each factor-of-10
  better/worse moves ±25 (a log-decade scale around the standard).
* **resilience** — worst Monte-Carlo year's unserved energy as ppm of demand (the
  Dunkelflaute tail the block-bootstrap ensemble exists to expose), against the 0.002 %
  (= 20 ppm) unserved-energy form of the reliability standard used by AEMO: 20 ppm scores 75,
  ±25 per decade.
* **independence** — 1 − import dependency (share of generation burning net-imported fuel,
  UN Comtrade-derived per-generator ``import_fuel_fraction``), linear.
* **climate** — emission intensity (gCO₂/kWh), piecewise-linear through two sourced anchors:
  the ~50 g/kWh 1.5 °C-aligned 2030 power-sector benchmark (IEA Net Zero Emissions scenario)
  scores 75, a pure coal-steam fleet (920 g ≈ the profile coal EF of 0.92 tCO₂/MWh) scores 0,
  and the headroom below 50 g earns the top quartile linearly down to 0 g → 100.

The six axes fold into the classic trilemma pillars — security = mean(reliability, resilience,
independence), equity = mean(affordability, price_stability), sustainability = climate — for a
WEC-comparable headline while staying expandable to the detailed hexagon.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

BENCHMARKS_PATH = Path(__file__).resolve().parents[1] / "data" / "radar_benchmarks.json"

# ── Sourced scoring anchors ──────────────────────────────────────────────────────
# "Meet the standard → 75" is deliberate and shared by every standards-anchored axis:
# compliance earns a solid score, headroom beyond the standard earns the top quartile.
LOLE_STANDARD_HOURS = 2.4        # "1 day in 10 years" planning standard (NERC/MISO practice)
UNSERVED_STANDARD_PPM = 20.0     # AEMO reliability standard: 0.002 % of demand unserved
SCORE_AT_STANDARD = 75.0         # score exactly at the standard
SCORE_PER_DECADE = 25.0          # log-decade slope around the standard
CLIMATE_BENCHMARK_G = 50.0       # 1.5 °C-aligned 2030 power-sector intensity (IEA NZE), g/kWh
CLIMATE_WORST_G = 920.0          # pure coal-steam fleet: profile coal EF 0.92 tCO2/MWh
# Documented fallback when radar_benchmarks.json is absent (fresh checkout before the build
# script runs): linear between the cheapest and priciest baseline LCOEs seen across the
# 164-country roster (rounded outward); the empirical CDF replaces this once the file exists.
AFFORDABILITY_FALLBACK_RANGE = (30.0, 200.0)  # $/MWh

AXIS_KEYS = (
    "affordability", "price_stability", "reliability",
    "resilience", "independence", "climate",
)
AXIS_LABELS = {
    "affordability": "Affordability",
    "price_stability": "Price stability",
    "reliability": "Reliability",
    "resilience": "Resilience",
    "independence": "Independence",
    "climate": "Climate",
}
PILLARS = {
    "security": ("reliability", "resilience", "independence"),
    "equity": ("affordability", "price_stability"),
    "sustainability": ("climate",),
}


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def score_log_decades(value: float, standard: float) -> float:
    r"""Score a smaller-is-better metric on a log-decade scale around a standard.

    Algorithm:
        $$s = \mathrm{clip}\left(75 - 25\,\log_{10}\frac{v}{v_{std}},\ 0,\ 100\right)$$
    ASCII: s = clip(75 - 25*log10(v / v_std), 0, 100); v <= 0 scores 100.

    ``v`` = metric value, ``v_std`` = the planning standard (same units). Meeting the standard
    exactly scores 75; each factor of 10 better/worse moves the score ±25.
    """
    if value <= 0.0:
        return 100.0
    return _clip(SCORE_AT_STANDARD - SCORE_PER_DECADE * math.log10(value / standard))


def score_affordability(system_lcoe: float, lcoe_distribution: list[float] | None) -> float:
    r"""Complement of the empirical-CDF position within the all-country baseline distribution.

    Algorithm:
        $$s = 100\,(1 - \hat F(L)),\qquad \hat F = \text{interpolated ECDF of baseline LCOEs}$$
    ASCII: s = 100 * (1 - ECDF(L)); cheapest observed ~100, priciest ~0.

    ``L`` = scenario system LCOE ($/MWh). Without a distribution (benchmarks file not yet
    built) falls back to linear between the documented roster-wide bounds.
    """
    if not lcoe_distribution:
        lo, hi = AFFORDABILITY_FALLBACK_RANGE
        return _clip(100.0 * (hi - system_lcoe) / (hi - lo))
    xs = sorted(lcoe_distribution)
    if len(xs) == 1:
        return 50.0
    if system_lcoe <= xs[0]:
        return 100.0
    if system_lcoe >= xs[-1]:
        return 0.0
    # interpolated ECDF: rank position of L among the sorted baseline LCOEs, in [0, 1]
    n = len(xs)
    for i in range(1, n):
        if system_lcoe <= xs[i]:
            frac = (system_lcoe - xs[i - 1]) / (xs[i] - xs[i - 1])
            q = ((i - 1) + frac) / (n - 1)
            return _clip(100.0 * (1.0 - q))
    return 0.0  # pragma: no cover — unreachable given the bounds checks


def score_price_stability(fuel_lcoe: float, system_lcoe: float) -> float:
    r"""Linear complement of the fuel share of system LCOE.

    Algorithm:
        $$s = 100\,\left(1 - \mathrm{clip}\!\left(\frac{C_{fuel}}{L},\,0,\,1\right)\right)$$
    ASCII: s = 100 * (1 - clip(fuel_lcoe / system_lcoe, 0, 1)).

    ``C_fuel`` = fuel component of the LCOE stack ($/MWh), ``L`` = system LCOE ($/MWh). Fuel is
    the market-priced, shock-exposed component; everything else is contracted capex/O&M.
    """
    if system_lcoe <= 0.0:
        return 100.0
    return _clip(100.0 * (1.0 - _clip(fuel_lcoe / system_lcoe, 0.0, 1.0)))


def score_independence(import_dependency: float) -> float:
    r"""Linear complement of import dependency.

    Algorithm:
        $$s = 100\,(1 - \mathrm{clip}(d,\,0,\,1))$$
    ASCII: s = 100 * (1 - clip(d, 0, 1)); d = share of generation burning net-imported fuel.
    """
    return _clip(100.0 * (1.0 - _clip(import_dependency, 0.0, 1.0)))


def score_climate(intensity_g_kwh: float) -> float:
    r"""Piecewise-linear score through the 1.5 °C benchmark and the pure-coal anchor.

    Algorithm:
        $$s = \begin{cases}
          100 - 25\,g/g_{1.5}, & g \le g_{1.5}\\[2pt]
          75\,\dfrac{g_{coal} - g}{g_{coal} - g_{1.5}}, & g > g_{1.5}
        \end{cases}$$
    ASCII: g <= 50: s = 100 - 25*g/50; else s = 75 * (920 - g) / (920 - 50), clipped to [0, 100].

    ``g`` = emission intensity (gCO2/kWh); ``g_1.5`` = 50 (IEA NZE 2030 power benchmark) scores
    75; ``g_coal`` = 920 (profile coal EF 0.92 tCO2/MWh) scores 0.
    """
    g = max(intensity_g_kwh, 0.0)
    if g <= CLIMATE_BENCHMARK_G:
        return _clip(100.0 - (100.0 - SCORE_AT_STANDARD) * g / CLIMATE_BENCHMARK_G)
    return _clip(SCORE_AT_STANDARD * (CLIMATE_WORST_G - g) / (CLIMATE_WORST_G - CLIMATE_BENCHMARK_G))


@lru_cache(maxsize=1)
def load_radar_benchmarks() -> dict[str, Any]:
    """Cached load of the one-time all-country baseline benchmarks (empty dict if absent)."""
    if BENCHMARKS_PATH.exists():
        return dict(json.loads(BENCHMARKS_PATH.read_text()))
    return {}


def compute_axes(metrics: dict[str, Any],
                 lcoe_distribution: list[float] | None) -> list[dict[str, Any]]:
    """Score the six radar axes from an engine-result metrics dict.

    Args:
        metrics: Needs ``system_lcoe`` ($/MWh), ``stack_components.fuel`` ($/MWh),
            ``emission_intensity`` (tCO2/MWh), ``import_dependency`` (0–1),
            ``annual_demand_twh`` (TWh), ``unserved_twh`` (TWh) and optionally ``adequacy``
            (the ensemble block with ``lole_hours`` and ``unserved_mwh_max``).
        lcoe_distribution: Sorted baseline system LCOEs across the country roster ($/MWh),
            for the affordability ECDF; None falls back to the documented linear range.

    Returns:
        One dict per axis: ``key``, ``label``, ``score`` (0–100), ``value`` (the raw sourced
        number), ``unit``, and ``detail`` (how the value became the score).
    """
    system_lcoe = float(metrics["system_lcoe"])
    fuel_lcoe = float(metrics.get("stack_components", {}).get("fuel", 0.0))
    intensity_g = float(metrics["emission_intensity"]) * 1000.0  # tCO2/MWh → gCO2/kWh
    import_dep = float(metrics.get("import_dependency", 0.0))
    demand_mwh = float(metrics.get("annual_demand_twh", 0.0)) * 1e6
    adequacy = metrics.get("adequacy") or None

    fuel_share = _clip(fuel_lcoe / system_lcoe, 0.0, 1.0) if system_lcoe > 0 else 0.0

    if adequacy is not None:
        lole = float(adequacy["lole_hours"])
        reliability_score = score_log_decades(lole, LOLE_STANDARD_HOURS)
        reliability_value, reliability_unit = lole, "h/yr LOLE"
        reliability_detail = (
            f"LOLE {lole:.2f} h/yr vs the 1-day-in-10-years standard ({LOLE_STANDARD_HOURS} "
            f"h/yr = 75); ±25 per factor of 10 ({adequacy.get('n_scenarios', 1)}-scenario ensemble)"
        )
        worst_mwh = float(adequacy.get("unserved_mwh_max", 0.0))
        resilience_basis = f"worst of {adequacy.get('n_scenarios', 1)} weather scenarios"
    else:
        # Single deterministic run: no scenario distribution, so both adequacy axes read the
        # one year's unserved energy (reliability = resilience until an ensemble is requested).
        worst_mwh = float(metrics.get("unserved_twh", 0.0)) * 1e6
        unserved_ppm = (worst_mwh / demand_mwh * 1e6) if demand_mwh > 0 else 0.0
        reliability_score = score_log_decades(unserved_ppm, UNSERVED_STANDARD_PPM)
        reliability_value, reliability_unit = unserved_ppm, "ppm unserved"
        reliability_detail = (
            f"single-run unserved energy {unserved_ppm:.1f} ppm of demand vs the 0.002% "
            f"(20 ppm) standard = 75; run an ensemble for a true LOLE"
        )
        resilience_basis = "single run (no weather ensemble)"

    worst_ppm = (worst_mwh / demand_mwh * 1e6) if demand_mwh > 0 else 0.0

    axes = [
        {
            "key": "affordability",
            "score": score_affordability(system_lcoe, lcoe_distribution),
            "value": system_lcoe,
            "unit": "$/MWh",
            "detail": (
                f"system LCOE {system_lcoe:.1f} $/MWh ranked against the all-country "
                f"baseline distribution (cheapest = 100, priciest = 0)"
                if lcoe_distribution else
                f"system LCOE {system_lcoe:.1f} $/MWh on the fallback linear scale "
                f"{AFFORDABILITY_FALLBACK_RANGE[0]:.0f}–{AFFORDABILITY_FALLBACK_RANGE[1]:.0f} "
                f"$/MWh (benchmarks file not built)"
            ),
        },
        {
            "key": "price_stability",
            "score": score_price_stability(fuel_lcoe, system_lcoe),
            "value": 100.0 * fuel_share,
            "unit": "% of LCOE from fuel",
            "detail": (
                f"fuel is {100.0 * fuel_share:.1f}% of system LCOE — the market-priced, "
                f"shock-exposed component; 0% fuel = 100"
            ),
        },
        {
            "key": "reliability",
            "score": reliability_score,
            "value": reliability_value,
            "unit": reliability_unit,
            "detail": reliability_detail,
        },
        {
            "key": "resilience",
            "score": score_log_decades(worst_ppm, UNSERVED_STANDARD_PPM),
            "value": worst_ppm,
            "unit": "ppm unserved (worst year)",
            "detail": (
                f"{resilience_basis}: {worst_ppm:.1f} ppm of demand unserved vs the 0.002% "
                f"(20 ppm) unserved-energy standard = 75; ±25 per factor of 10"
            ),
        },
        {
            "key": "independence",
            "score": score_independence(import_dep),
            "value": 100.0 * _clip(import_dep, 0.0, 1.0),
            "unit": "% generation on imported fuel",
            "detail": (
                f"{100.0 * import_dep:.1f}% of generation burns net-imported fuel "
                f"(UN Comtrade net fuel trade per country); 0% = 100"
            ),
        },
        {
            "key": "climate",
            "score": score_climate(intensity_g),
            "value": intensity_g,
            "unit": "gCO₂/kWh",
            "detail": (
                f"emission intensity {intensity_g:.0f} g/kWh; 1.5 °C-aligned 2030 power "
                f"benchmark ({CLIMATE_BENCHMARK_G:.0f} g, IEA NZE) = 75, coal fleet "
                f"({CLIMATE_WORST_G:.0f} g) = 0"
            ),
        },
    ]
    for axis in axes:
        axis["label"] = AXIS_LABELS[axis["key"]]
        axis["score"] = round(float(axis["score"]), 1)
        axis["value"] = round(float(axis["value"]), 2)
    return axes


def fold_pillars(axes: list[dict[str, Any]]) -> dict[str, float]:
    """Fold the six axes into the WEC-comparable trilemma pillars (unweighted means)."""
    by_key = {axis["key"]: float(axis["score"]) for axis in axes}
    return {
        pillar: round(sum(by_key[k] for k in keys) / len(keys), 1)
        for pillar, keys in PILLARS.items()
    }


def build_radar(metrics: dict[str, Any]) -> dict[str, Any]:
    """Assemble the radar block for one engine result: scenario axes + country baseline.

    The baseline polygon (the country's real Ember mix at zero carbon price, default demand,
    5-member block-bootstrap ensemble) comes precomputed from ``radar_benchmarks.json`` so it
    costs nothing at request time; the scenario polygon is scored live from ``metrics``.
    """
    benchmarks = load_radar_benchmarks()
    distribution = benchmarks.get("lcoe_distribution") or None
    axes = compute_axes(metrics, distribution)
    baseline_entry = benchmarks.get("countries", {}).get(str(metrics.get("country", "")).upper())
    baseline = None
    if baseline_entry is not None:
        baseline = {
            "axes": baseline_entry["axes"],
            "note": benchmarks.get("baseline_note", ""),
        }
    return {
        "axes": axes,
        "pillars": fold_pillars(axes),
        "baseline": baseline,
        "method": (
            "Six 0–100 axes scored from this scenario's engine outputs: physical axes against "
            "engineering standards (LOLE 2.4 h/yr, 20 ppm unserved, 50 gCO₂/kWh 1.5 °C "
            "benchmark), economic axes against the all-country baseline LCOE distribution; "
            "pillars are unweighted means (security/equity/sustainability)."
        ),
    }
