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
# Technology *structure* (dispatch functions, heat rates, emission factors, storage, and the
# capex/opex base levels) comes from the shared literature template (TEMPLATE_COUNTRY's profile).
# On top of that, three cost levers that genuinely vary by market are applied per **region**:
# delivered fuel prices (gas/coal), the discount rate (WACC), and a VRE capex multiplier.

# Each country's region. Regions group markets with similar delivered fuel prices and cost of
# capital.
COUNTRY_REGION: dict[str, str] = {
    "US": "north_america", "CA": "north_america", "MX": "north_america",
    "DE": "europe", "FR": "europe", "GB": "europe", "ES": "europe", "IT": "europe",
    "NL": "europe", "PL": "europe", "SE": "europe", "FI": "europe", "DK": "europe",
    "NO": "europe", "IE": "europe",
    "KR": "ne_asia_adv", "JP": "ne_asia_adv", "TW": "ne_asia_adv",
    "CN": "china", "IN": "south_asia",
    "ID": "se_asia", "MY": "se_asia", "PH": "se_asia", "TH": "se_asia", "VN": "se_asia",
    "AE": "middle_east", "SA": "middle_east",
    "AU": "oceania", "TR": "emerging_europe",
    "BR": "lat_am", "AR": "lat_am", "CL": "lat_am", "ZA": "africa",
}

# Regional cost levers. Fuel prices (USD/MMBtu, delivered) and WACC follow IEA WEO 2024 regional
# assumptions and market benchmarks (US Henry Hub / Asian LNG / European TTF for gas); the VRE
# capex multiplier scales the template solar/wind capex by IRENA 2024 regional cost ratios (China
# lowest, OECD ~1.0). nuclear and "other" fuel stay at the template value.
REGIONS: dict[str, dict[str, float]] = {
    #                     gas   coal   wacc   vre_capex_mult
    "north_america":  {"gas": 3.5,  "coal": 2.0, "wacc": 0.055, "vre_mult": 1.00},
    "europe":         {"gas": 11.0, "coal": 4.5, "wacc": 0.050, "vre_mult": 1.05},
    "ne_asia_adv":    {"gas": 13.5, "coal": 4.5, "wacc": 0.055, "vre_mult": 1.00},
    "china":          {"gas": 9.0,  "coal": 4.0, "wacc": 0.070, "vre_mult": 0.75},
    "south_asia":     {"gas": 11.0, "coal": 3.0, "wacc": 0.090, "vre_mult": 0.80},
    "se_asia":        {"gas": 9.0,  "coal": 3.5, "wacc": 0.085, "vre_mult": 0.90},
    "middle_east":    {"gas": 3.0,  "coal": 5.0, "wacc": 0.070, "vre_mult": 0.95},
    "oceania":        {"gas": 8.5,  "coal": 2.0, "wacc": 0.060, "vre_mult": 1.05},
    "lat_am":         {"gas": 7.0,  "coal": 4.0, "wacc": 0.090, "vre_mult": 1.00},
    "africa":         {"gas": 6.0,  "coal": 2.0, "wacc": 0.100, "vre_mult": 1.00},
    "emerging_europe": {"gas": 11.0, "coal": 4.5, "wacc": 0.100, "vre_mult": 1.00},
}

# ── Offshore wind split ──────────────────────────────────────────────────────────
# Ember reports a single "Wind" figure. We carve offshore out of it using a curated
# offshore-capacity table (GWEC Global Wind Report 2024 / IRENA statistics, end-2024, GW), then
# attribute generation at an assumed offshore capacity factor and leave the remainder to onshore.
# Countries not listed have no material offshore fleet (offshore capacity 0).
OFFSHORE_CAPACITY_GW: dict[str, float] = {
    "CN": 38.0, "GB": 14.7, "DE": 8.5, "NL": 4.7, "DK": 2.7, "TW": 2.4, "FR": 1.5,
    "VN": 0.9, "JP": 0.2, "SE": 0.19, "KR": 0.14, "NO": 0.09, "FI": 0.07, "US": 0.04,
    "IE": 0.03, "IT": 0.03,
}
OFFSHORE_CF_RATIO = 1.5       # offshore capacity factor ≈ 1.5 × onshore (IEA/IRENA); the split
                              # conserves Ember's total wind energy at this ratio
OFFSHORE_CF_FALLBACK = 0.42   # display CF for a zero-capacity offshore block
OFFSHORE_CF_BOUNDS = (0.25, 0.60)
OFFSHORE_CAPEX_USD_KW = 3500  # fixed-bottom offshore capex, IRENA 2024 / NREL ATB 2024
OFFSHORE_OPEX_USD_KW_YR = 80
OFFSHORE_VARIABILITY = 0.75   # offshore output is smoother than onshore
# ── Per-technology ramp rates (fraction of nameplate per hour) ──────────────────────────────────
# The most a unit's output may move between adjacent hours, as a share of its rated capacity. These
# are the config-backed DEFAULTS written into every profile's flexible-thermal generator blocks; the
# user can edit them per country in Parameters, or a caller can override per generator via the
# ramp_up / ramp_down request fields. Only the dispatchable thermals the merit stack actually ramps
# carry a rate — nuclear runs as flat must-run baseload (no ramp modelled) and VRE follows the
# weather, so neither is listed. Values are stylized from unit-flexibility literature (typical hourly
# ramp capability): CCGT fast, hard coal moderate, the mixed "other" bucket (hydro/bioenergy/peakers)
# fast. At hourly resolution these bind mainly for slow units on the steep evening net-load ramp.
RAMP_DEFAULTS: dict[str, dict[str, float]] = {
    "gas_ccgt": {"up": 0.8, "down": 0.8},   # combined-cycle gas: ~3–8 %/min ⇒ generous at 1 h steps
    "coal": {"up": 0.5, "down": 0.5},       # hard coal / lignite: slower, chases the cliff worse
    "other": {"up": 0.7, "down": 0.7},      # hydro/bioenergy/OCGT peaker mix: fairly flexible
}
SOURCE_RAMP = (
    "Per-technology ramp rates (fraction of nameplate per hour) stylized from IEA/NREL unit-"
    "flexibility literature; nuclear runs as flat baseload and VRE is weather-driven (no ramp limit)"
)

SOURCE_OFFSHORE = (
    "Offshore wind capacity split from GWEC Global Wind Report 2024 / IRENA statistics; offshore "
    "costs from IRENA 2024 / NREL ATB 2024"
)

SOURCE_EMBER = (
    "Ember Yearly Electricity Data (full release), "
    "https://ember-energy.org/data/yearly-electricity-data/ — "
    "demand, installed capacity and generation by fuel (CC-BY-4.0)"
)
SOURCE_COSTS = (
    "Technology structure (heat rates, dispatch/curtailment functions, storage): IEA WEO 2024 / "
    "IRENA Renewable Power Generation Costs 2024 (shared template)"
)
SOURCE_REGIONAL = (
    "Regional cost levers — delivered gas/coal prices, discount rate (WACC) and a VRE capex "
    "multiplier — from IEA WEO 2024 regional assumptions and IRENA 2024 (see COUNTRY_REGION)"
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

    # Regional cost levers: WACC, delivered gas/coal price, and a VRE capex multiplier.
    region = REGIONS[COUNTRY_REGION[code]]
    profile["discount_rate"] = region["wacc"]
    profile["region"] = COUNTRY_REGION[code]
    profile["generators"]["gas_ccgt"]["fuel_usd_mmbtu"] = region["gas"]
    profile["generators"]["coal"]["fuel_usd_mmbtu"] = region["coal"]
    for vre in ("solar", "wind_onshore"):
        base_capex = float(template["generators"][vre]["capex_usd_kw"])
        profile["generators"][vre]["capex_usd_kw"] = round(base_capex * region["vre_mult"], 1)

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

    _split_offshore_wind(code, profile, template, capacities, shares, data, total_gen, region)

    # Config-backed per-technology ramp rates onto the flexible-thermal blocks (editable in the UI).
    for tech, rates in RAMP_DEFAULTS.items():
        if tech in profile["generators"]:
            profile["generators"][tech]["ramp_up_frac_per_hr"] = rates["up"]
            profile["generators"][tech]["ramp_down_frac_per_hr"] = rates["down"]

    profile["capacities_gw"] = capacities
    profile["shares"] = shares
    profile["sources"] = [
        f"{SOURCE_EMBER}; data year {data['year']}",
        SOURCE_COSTS,
        SOURCE_REGIONAL,
        SOURCE_OFFSHORE,
        SOURCE_RAMP,
    ]
    return profile


def _anchor_cf_func(gen_block: dict[str, Any], cf: float) -> None:
    func = gen_block.get("cf_eff_func", {})
    if func.get("type") == "logarithmic" and "params" in func:
        func["params"]["a"] = cf


def _split_offshore_wind(code: str, profile: dict[str, Any], template: dict[str, Any],
                         capacities: dict[str, float], shares: dict[str, float],
                         data: dict[str, Any], total_gen: float, region: dict[str, float]) -> None:
    """Carve offshore out of the country's total wind and add a ``wind_offshore`` generator.

    The Ember "Wind" capacity/generation is treated as the total. Offshore capacity comes from the
    curated table; onshore and offshore capacity factors are then split so that offshore is
    ``OFFSHORE_CF_RATIO`` times onshore **while conserving Ember's total wind generation**
    (onshore_cap·cf + offshore_cap·ratio·cf = total wind energy). This keeps onshore realistic
    instead of starving it. Every country gets a wind_offshore block (0 GW where there is none) so
    the schema is uniform.
    """
    total_wind_cap = capacities["wind_onshore"]
    total_wind_gen = data["generation_twh"]["wind_onshore"]
    offshore_cap = round(min(OFFSHORE_CAPACITY_GW.get(code, 0.0), total_wind_cap), 3)
    onshore_cap = round(total_wind_cap - offshore_cap, 3)

    if offshore_cap >= MIN_CAPACITY_GW and onshore_cap >= MIN_CAPACITY_GW and total_wind_gen > 0:
        # Energy-conserving split at the offshore/onshore CF ratio.
        avg_gw = total_wind_gen * 1000.0 / HOURS_PER_YEAR
        onshore_cf = avg_gw / (onshore_cap + OFFSHORE_CF_RATIO * offshore_cap)
        offshore_cf = onshore_cf * OFFSHORE_CF_RATIO
        lo, hi = CF_BOUNDS["wind_onshore"]
        off_lo, off_hi = OFFSHORE_CF_BOUNDS
        onshore_cf = round(min(max(onshore_cf, lo), hi), 4)
        offshore_cf = round(min(max(offshore_cf, off_lo), off_hi), 4)
        onshore_gen = onshore_cap * onshore_cf * HOURS_PER_YEAR / 1000.0
        offshore_gen = offshore_cap * offshore_cf * HOURS_PER_YEAR / 1000.0
    else:
        # No material offshore fleet: give all wind back to onshore (keeping its Ember CF); the
        # offshore block is 0 GW.
        onshore_cap = total_wind_cap
        offshore_cap = 0.0
        onshore_cf = float(profile["generators"]["wind_onshore"]["cf_base"])
        offshore_cf = OFFSHORE_CF_FALLBACK
        onshore_gen = total_wind_gen
        offshore_gen = 0.0

    capacities["wind_onshore"] = onshore_cap
    shares["wind_onshore"] = round(onshore_gen / total_gen, 4)
    profile["generators"]["wind_onshore"]["cf_base"] = onshore_cf
    _anchor_cf_func(profile["generators"]["wind_onshore"], onshore_cf)

    # Build the offshore generator block from the onshore template with offshore costs/CF.
    offshore = json.loads(json.dumps(template["generators"]["wind_onshore"]))
    offshore["cf_base"] = offshore_cf
    offshore["capex_usd_kw"] = round(OFFSHORE_CAPEX_USD_KW * region["vre_mult"], 1)
    offshore["opex_fixed_usd_kw_yr"] = OFFSHORE_OPEX_USD_KW_YR
    offshore["variability_factor"] = OFFSHORE_VARIABILITY
    _anchor_cf_func(offshore, offshore_cf)
    profile["generators"]["wind_offshore"] = offshore

    capacities["wind_offshore"] = round(offshore_cap, 3)
    shares["wind_offshore"] = round(offshore_gen / total_gen, 4)


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
    manifest_countries: dict[str, dict[str, Any]] = {}
    for code, (iso3, name) in sorted(COUNTRIES.items()):
        data = extract_country(rows, iso3, year_cap)
        profile = build_profile(code, iso3, name, template, data)
        cf = profile["generators"]
        cg = profile["capacities_gw"]
        print(f"{code:<5}{data['year']:<6}{profile['annual_generation_twh']:>9.1f}"
              f"{cf['solar']['cf_base']:>10.3f}{cf['wind_onshore']['cf_base']:>9.3f}"
              f"{cg['solar']:>9.1f}{cg['wind_onshore']:>8.1f}{cg['gas_ccgt']:>8.1f}"
              f"{cg['coal']:>8.1f}{cg['nuclear']:>8.1f}")
        manifest_countries[code] = {
            "name": name, "iso3": iso3, "data_year": data["year"],
            "region": profile["region"], "demand_twh": profile["annual_demand_twh"],
        }
        if not args.check:
            (PROFILE_DIR / f"{code}.json").write_text(json.dumps(profile, indent=2) + "\n")
            written += 1

    if args.check:
        print("\n--check: no files written.")
        return 0

    # Deterministic provenance manifest (no build timestamp, so rebuilds stay clean in git).
    manifest = {
        "ember_source": EMBER_URL,
        "latest_settled_year": year_cap,
        "country_count": len(manifest_countries),
        "cost_source": SOURCE_REGIONAL,
        "countries": manifest_countries,
        "refresh": "python -m backend.data.build_country_profiles --download",
    }
    # Written to DATA_DIR (not PROFILE_DIR, which is globbed as country profiles).
    (DATA_DIR / "country_profiles_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote {written} profiles + country_profiles_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
