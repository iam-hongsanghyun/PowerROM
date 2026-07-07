"""Regenerate country profiles from the Ember Yearly Electricity Data release.

This is the single source of truth for the *country-specific* numbers in
``backend/data/country_profiles/*.json``: annual demand/generation, installed
capacity by technology, the default generation mix, and the real annual capacity
factors. Everything else in a profile (capex/opex, heat rates, dispatch/curtailment
functions, storage) is a **global, literature-cited engineering template** shared by
every country — those are technology parameters, not country data, so they are not
invented per country.

Data source
-----------
Ember, *Yearly Electricity Data* (long-format full release):
https://ember-energy.org/data/yearly-electricity-data/
Downloaded CSV cached at ``backend/data/ember/yearly_full_release_long_format.csv``
(git-ignored; re-download with ``--download``). Ember is CC-BY-4.0.

For each country we take the **latest year** that has demand, total generation and
installed-capacity records, then:

* ``annual_generation_twh`` = Ember "Total Generation" (TWh).
* ``capacities_gw[tech]``   = Ember installed capacity by fuel (GW), mapped to buckets.
* ``shares[tech]``          = Ember generation by fuel ÷ total generation (the default mix).
* ``generators[tech].cf_base`` = Ember generation ÷ (capacity × 8760 h) — the *real*
  annual capacity factor, clipped to a physical band. For solar and wind this is the
  observed country capacity factor; for thermal it is the observed fleet utilisation.

Run
---
    python -m backend.data.build_country_profiles              # rebuild all
    python -m backend.data.build_country_profiles --download   # refresh Ember CSV first
    python -m backend.data.build_country_profiles --check      # print table, write nothing
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

HOURS_PER_YEAR = 8760
DATA_DIR = Path(__file__).resolve().parent
PROFILE_DIR = DATA_DIR / "country_profiles"
EMBER_CSV = DATA_DIR / "ember" / "yearly_full_release_long_format.csv"
EMBER_URL = (
    "https://storage.googleapis.com/emb-prod-bkt-publicdata/"
    "public-downloads/yearly_full_release_long_format.csv"
)
TEMPLATE_COUNTRY = "KR"  # structural template: schema + global cost/function fields

# ── Country roster: 2-letter code → (Ember ISO-3, display name) ──────────────────
COUNTRIES: dict[str, tuple[str, str]] = {
    "AE": ("ARE", "United Arab Emirates"),
    "AR": ("ARG", "Argentina"),
    "AU": ("AUS", "Australia"),
    "BR": ("BRA", "Brazil"),
    "CA": ("CAN", "Canada"),
    "CL": ("CHL", "Chile"),
    "CN": ("CHN", "China"),
    "DE": ("DEU", "Germany"),
    "DK": ("DNK", "Denmark"),
    "ES": ("ESP", "Spain"),
    "FI": ("FIN", "Finland"),
    "FR": ("FRA", "France"),
    "GB": ("GBR", "United Kingdom"),
    "ID": ("IDN", "Indonesia"),
    "IE": ("IRL", "Ireland"),
    "IN": ("IND", "India"),
    "IT": ("ITA", "Italy"),
    "JP": ("JPN", "Japan"),
    "KR": ("KOR", "South Korea"),
    "MX": ("MEX", "Mexico"),
    "MY": ("MYS", "Malaysia"),
    "NL": ("NLD", "Netherlands"),
    "NO": ("NOR", "Norway"),
    "PH": ("PHL", "Philippines"),
    "PL": ("POL", "Poland"),
    "SA": ("SAU", "Saudi Arabia"),
    "SE": ("SWE", "Sweden"),
    "TH": ("THA", "Thailand"),
    "TR": ("TUR", "Turkey"),
    "TW": ("TWN", "Taiwan"),
    "US": ("USA", "United States"),
    "VN": ("VNM", "Vietnam"),
    "ZA": ("ZAF", "South Africa"),
}

# ── Ember "Fuel" → model generator bucket ───────────────────────────────────────
# The model has six buckets; "other" absorbs hydro, bioenergy and residual fossil,
# which the model treats as a dispatchable catch-all.
FUEL_TO_BUCKET: dict[str, str] = {
    "Solar": "solar",
    "Wind": "wind_onshore",
    "Gas": "gas_ccgt",
    "Coal": "coal",
    "Nuclear": "nuclear",
    "Hydro": "other",
    "Bioenergy": "other",
    "Other Fossil": "other",
    "Other Renewables": "other",  # geothermal etc. — without this, shares miss ~3% for TR/ID/IT/PH
}
BUCKETS = ["solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "other"]

# Physical capacity-factor bands. Ember gen÷cap is clipped into these so a country
# with a tiny/new fleet (division blow-ups) or a partial reporting year can't emit an
# absurd CF. Outside the band → clip; near-zero capacity → keep the template default.
CF_BOUNDS: dict[str, tuple[float, float]] = {
    "solar": (0.07, 0.30),
    "wind_onshore": (0.10, 0.55),
    "gas_ccgt": (0.10, 0.90),
    "coal": (0.10, 0.90),
    "nuclear": (0.40, 0.95),
    "other": (0.10, 0.85),
}
MIN_CAPACITY_GW = 0.20  # below this, CF is numerically unreliable → use template default

# ── Cost / fuel / discount template ─────────────────────────────────────────────
# This pass replaces only the *data* fields (demand, capacity, generation mix, capacity
# factors). Technology costs, fuel prices, dispatch functions and the discount rate are left
# at the shared literature-based template (TEMPLATE_COUNTRY's profile) — they are technology,
# not country, parameters. Real per-country cost/fuel differentiation would need a real cost
# source (IEA/IRENA country tables) and is out of scope here.

SOURCE_EMBER = (
    "Ember Yearly Electricity Data (full release), "
    "https://ember-energy.org/data/yearly-electricity-data/ — "
    "demand, installed capacity and generation by fuel (CC-BY-4.0)"
)
SOURCE_COSTS = (
    "Technology costs, fuel prices, discount rate & dispatch functions: IEA World Energy "
    "Outlook 2024 / IRENA Renewable Power Generation Costs 2024 (shared global template, "
    "identical across countries)"
)


def download_ember() -> None:
    EMBER_CSV.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Ember dataset → {EMBER_CSV} ...", file=sys.stderr)
    urllib.request.urlretrieve(EMBER_URL, EMBER_CSV)  # noqa: S310 (pinned https)


def load_ember() -> list[dict[str, str]]:
    if not EMBER_CSV.exists():
        download_ember()
    with EMBER_CSV.open(newline="") as f:
        return list(csv.DictReader(f))


def _f(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def latest_settled_year(rows: list[dict[str, str]]) -> int:
    """Latest calendar year Ember reports as a *settled* full year, not a provisional nowcast.

    Ember's long-format export tags its provisional current-year release identically to prior
    complete years, but that year exists for only a fraction of countries and is a partial,
    seasonally-skewed sum. We treat a year as settled when its global country coverage (distinct
    ISO-3 codes with a Total Generation row) is at least 60% of the best-covered year, and cap
    all country year-selection at the latest settled year.
    """
    cov: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        if (
            row["Category"] == "Electricity generation"
            and row["Subcategory"] == "Total"
            and row["Unit"] == "TWh"
        ):
            cov[int(row["Year"])].add(row["ISO 3 code"])
    counts = {y: len(s) for y, s in cov.items()}
    threshold = 0.6 * max(counts.values())
    return max(y for y, c in counts.items() if c >= threshold)


def extract_country(rows: list[dict[str, str]], iso3: str, year_cap: int) -> dict[str, Any]:
    """Pull demand, capacity-by-fuel and generation-by-fuel for the latest settled full year.

    Returns a dict with keys: ``year``, ``demand_twh``, ``total_gen_twh``,
    ``capacity_gw`` (bucket→GW), ``generation_twh`` (bucket→TWh). ``year_cap`` is the latest
    settled year (see :func:`latest_settled_year`); years after it are ignored.
    """
    # year → various records
    demand: dict[int, float] = {}
    total_gen: dict[int, float] = {}
    cap: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    gen: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for row in rows:
        if row["ISO 3 code"] != iso3:
            continue
        year = int(row["Year"])
        cat, sub, var, unit = row["Category"], row["Subcategory"], row["Variable"], row["Unit"]
        val = _f(row["Value"])
        if cat == "Electricity demand" and sub == "Demand" and var == "Demand" and unit == "TWh":
            demand[year] = val
        elif cat == "Electricity generation" and sub == "Total" and unit == "TWh":
            total_gen[year] = val
        elif cat == "Capacity" and sub == "Fuel" and unit == "GW" and var in FUEL_TO_BUCKET:
            cap[year][FUEL_TO_BUCKET[var]] += val
        elif cat == "Electricity generation" and sub == "Fuel" and unit == "TWh" and var in FUEL_TO_BUCKET:
            gen[year][FUEL_TO_BUCKET[var]] += val

    # Latest *settled* year with demand + total generation + some capacity reported.
    # `year_cap` excludes Ember's provisional current-year release: that year is present for
    # only ~40% of countries and is a summer-weighted partial sum, so it inflates VRE
    # generation/CF (e.g. NL 2025 solar CF 0.126 vs its settled ~0.101 trend). See
    # latest_settled_year().
    candidate_years = sorted(
        y for y in total_gen
        if y <= year_cap and y in demand and y in cap and sum(cap[y].values()) > 0
    )
    if not candidate_years:
        raise ValueError(f"No complete year for {iso3}")
    year = candidate_years[-1]
    return {
        "year": year,
        "demand_twh": demand[year],
        "total_gen_twh": total_gen[year],
        "capacity_gw": {b: cap[year].get(b, 0.0) for b in BUCKETS},
        "generation_twh": {b: gen[year].get(b, 0.0) for b in BUCKETS},
    }


def real_capacity_factor(bucket: str, gen_twh: float, cap_gw: float, template_cf: float) -> float:
    """Annual CF = generation ÷ (capacity × 8760 h), clipped to a physical band.

    Falls back to the template CF when capacity is too small for the ratio to be
    meaningful (new/negligible fleet).
    """
    if cap_gw < MIN_CAPACITY_GW or gen_twh <= 0:
        return template_cf
    cf = (gen_twh * 1000.0) / (cap_gw * HOURS_PER_YEAR)  # TWh→GWh ÷ GW·h
    lo, hi = CF_BOUNDS[bucket]
    return round(min(max(cf, lo), hi), 4)


def build_profile(code: str, iso3: str, name: str, template: dict[str, Any],
                  data: dict[str, Any]) -> dict[str, Any]:
    profile = json.loads(json.dumps(template))  # deep copy of structural template
    profile["name"] = name
    profile["data_year"] = data["year"]
    profile["annual_generation_twh"] = round(data["total_gen_twh"], 2)
    # Ember's electricity-demand series (generation ± net imports). The UI seeds its "Annual
    # Demand" input from this so net-importing countries (e.g. AR, GB) show true demand, not
    # just domestic generation.
    profile["annual_demand_twh"] = round(data["demand_twh"], 2)
    # discount_rate and all cost/fuel fields are left at the template value (see SOURCE_COSTS).

    total_gen = data["total_gen_twh"] or 1.0
    capacities: dict[str, float] = {}
    shares: dict[str, float] = {}
    for bucket in BUCKETS:
        cap_gw = data["capacity_gw"][bucket]
        gen_twh = data["generation_twh"][bucket]
        capacities[bucket] = round(cap_gw, 3)
        shares[bucket] = round(max(gen_twh, 0.0) / total_gen, 4)

        gen_block = profile["generators"][bucket]
        template_cf = float(gen_block.get("cf_base", 0.5))
        cf = real_capacity_factor(bucket, gen_twh, cap_gw, template_cf)
        gen_block["cf_base"] = cf
        # anchor the CF-vs-VRE-share curve at the real base CF for the VRE techs
        if bucket in ("solar", "wind_onshore"):
            func = gen_block.get("cf_eff_func", {})
            if func.get("type") == "logarithmic" and "params" in func:
                func["params"]["a"] = cf
                func["source"] = "Base CF from Ember; degradation-with-share stylized (IEA/IRENA)"

    profile["capacities_gw"] = capacities
    profile["shares"] = shares
    profile["sources"] = [
        f"{SOURCE_EMBER}; data year {data['year']}",
        SOURCE_COSTS,
    ]
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="refresh the Ember CSV first")
    parser.add_argument("--check", action="store_true", help="print summary, write nothing")
    args = parser.parse_args()

    if args.download:
        download_ember()

    rows = load_ember()
    template = json.loads((PROFILE_DIR / f"{TEMPLATE_COUNTRY}.json").read_text())
    year_cap = latest_settled_year(rows)
    print(f"Latest settled Ember year (provisional years excluded): {year_cap}\n")

    print(f"{'code':<5}{'yr':<6}{'demand':>9}{'solar_cf':>10}{'wind_cf':>9}"
          f"{'solarGW':>9}{'windGW':>8}{'gasGW':>8}{'coalGW':>8}{'nucGW':>8}")
    written = 0
    for code, (iso3, name) in sorted(COUNTRIES.items()):
        data = extract_country(rows, iso3, year_cap)
        profile = build_profile(code, iso3, name, template, data)
        cf = profile["generators"]
        cg = profile["capacities_gw"]
        print(f"{code:<5}{data['year']:<6}{profile['annual_generation_twh']:>9.1f}"
              f"{cf['solar']['cf_base']:>10.3f}{cf['wind_onshore']['cf_base']:>9.3f}"
              f"{cg['solar']:>9.1f}{cg['wind_onshore']:>8.1f}{cg['gas_ccgt']:>8.1f}"
              f"{cg['coal']:>8.1f}{cg['nuclear']:>8.1f}")
        if not args.check:
            (PROFILE_DIR / f"{code}.json").write_text(json.dumps(profile, indent=2) + "\n")
            written += 1

    if args.check:
        print("\n--check: no files written.")
    else:
        print(f"\nWrote {written} profiles to {PROFILE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
