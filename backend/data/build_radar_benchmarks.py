"""Build ``radar_benchmarks.json``: every country's baseline radar polygon + LCOE distribution.

The System Radar (``backend.core.radar``) needs two things it cannot compute per request:

1. the **empirical baseline LCOE distribution** across the whole country roster — the
   affordability axis scores a scenario's LCOE by its rank within it, and
2. each country's **baseline polygon** — its real Ember mix, zero carbon price, default
   demand — drawn under the user's scenario so the gap between the two polygons is the
   policy story.

Both come from one engine run per country at the fixed baseline configuration below (the same
machinery the tests exercise), so the file is a pure derivative of the country profiles:
rebuild it whenever the profiles change.

Run:
    python -m backend.data.build_radar_benchmarks
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from backend.core.lcoe_engine import calculate_system_lcoe, load_country_profile
from backend.core.radar import compute_axes, fold_pillars

DATA_DIR = Path(__file__).resolve().parent
MANIFEST = DATA_DIR / "country_profiles_manifest.json"
OUT_PATH = DATA_DIR / "radar_benchmarks.json"

# Baseline scenario: the country as it is — real Ember installed capacities and demand,
# real weather years (data mode; countries without hourly files fall back to the parametric
# synthesizer, exactly as the UI does), no policy levers, no added storage — with a
# fixed-seed block-bootstrap weather ensemble so the two adequacy axes read a real scenario
# distribution. This mirrors how the UI evaluates an untouched country, so the baseline
# polygon is directly comparable to the scenario polygon drawn over it. 5 members keeps the
# full-roster build to a few minutes.
BASELINE_CARBON_PRICE = 0.0
BASELINE_DISPATCH_MODE = "data"
BASELINE_ENSEMBLE: dict[str, Any] = {
    "method": "block_bootstrap", "n_samples": 5, "seed": 42, "block_days": 14,
}
BASELINE_NOTE = (
    "Country baseline: real Ember installed capacities and demand, real weather years, "
    "zero carbon price, no added storage, 5-member block-bootstrap weather ensemble (seed 42)."
)


def baseline_metrics(code: str) -> dict[str, Any]:
    """One baseline engine run → the raw metrics the radar axes need."""
    profile = load_country_profile(code)
    result = calculate_system_lcoe(
        country=code,
        shares={},
        capacities_gw=profile["capacities_gw"],
        annual_demand_twh=profile.get("annual_demand_twh") or profile["annual_generation_twh"],
        carbon_price=BASELINE_CARBON_PRICE,
        dispatch_mode=BASELINE_DISPATCH_MODE,
        ensemble=BASELINE_ENSEMBLE,
    )
    return {
        "country": code,
        "system_lcoe": result["system_lcoe"],
        "stack_components": {"fuel": result["stack_components"]["fuel"]},
        "emission_intensity": result["emission_intensity"],
        "import_dependency": result["import_dependency"],
        "annual_demand_twh": result["annual_demand_twh"],
        "unserved_twh": result["unserved_twh"],
        "adequacy": {
            key: result["adequacy"][key]
            for key in ("lole_hours", "unserved_mwh_max", "n_scenarios")
        } if result.get("adequacy") else None,
    }


def main() -> int:
    codes = sorted(json.loads(MANIFEST.read_text())["countries"])

    # Pass 1 — run every country's baseline and collect raw metrics.
    metrics: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for i, code in enumerate(codes, 1):
        try:
            metrics[code] = baseline_metrics(code)
        except Exception as exc:  # noqa: BLE001 — report and continue the batch
            failed.append(code)
            print(f"{code}  ERROR: {exc}", file=sys.stderr)
            continue
        print(f"[{i}/{len(codes)}] {code}  lcoe={metrics[code]['system_lcoe']:.1f}")

    # Pass 2 — the affordability axis needs the full distribution, so score after collecting.
    distribution = sorted(m["system_lcoe"] for m in metrics.values())
    countries: dict[str, Any] = {}
    for code, m in metrics.items():
        axes = compute_axes(m, distribution)
        countries[code] = {
            "axes": axes,
            "pillars": fold_pillars(axes),
            "system_lcoe": round(m["system_lcoe"], 2),
        }

    OUT_PATH.write_text(json.dumps({
        "baseline_note": BASELINE_NOTE,
        "baseline_config": {"carbon_price": BASELINE_CARBON_PRICE, "ensemble": BASELINE_ENSEMBLE},
        "failed": sorted(failed),
        "lcoe_distribution": [round(x, 2) for x in distribution],
        "countries": countries,
    }, indent=2) + "\n")
    print(f"\nWrote {len(countries)} country baselines ({len(failed)} failed) → {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
