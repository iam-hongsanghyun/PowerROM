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
import math
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
    "AF": ("AFG", "Afghanistan"),
    "AM": ("ARM", "Armenia"),
    "AO": ("AGO", "Angola"),
    "AR": ("ARG", "Argentina"),
    "AT": ("AUT", "Austria"),
    "AU": ("AUS", "Australia"),
    "AW": ("ABW", "Aruba"),
    "AZ": ("AZE", "Azerbaijan"),
    "BA": ("BIH", "Bosnia and Herzegovina"),
    "BB": ("BRB", "Barbados"),
    "BD": ("BGD", "Bangladesh"),
    "BE": ("BEL", "Belgium"),
    "BF": ("BFA", "Burkina Faso"),
    "BG": ("BGR", "Bulgaria"),
    "BH": ("BHR", "Bahrain"),
    "BJ": ("BEN", "Benin"),
    "BN": ("BRN", "Brunei"),
    "BO": ("BOL", "Bolivia"),
    "BR": ("BRA", "Brazil"),
    "BS": ("BHS", "Bahamas"),
    "BT": ("BTN", "Bhutan"),
    "BW": ("BWA", "Botswana"),
    "BY": ("BLR", "Belarus"),
    "CA": ("CAN", "Canada"),
    "CD": ("COD", "DR Congo"),
    "CG": ("COG", "Congo"),
    "CH": ("CHE", "Switzerland"),
    "CI": ("CIV", "Cote d'Ivoire"),
    "CL": ("CHL", "Chile"),
    "CM": ("CMR", "Cameroon"),
    "CN": ("CHN", "China"),
    "CO": ("COL", "Colombia"),
    "CR": ("CRI", "Costa Rica"),
    "CU": ("CUB", "Cuba"),
    "CY": ("CYP", "Cyprus"),
    "CZ": ("CZE", "Czechia"),
    "DE": ("DEU", "Germany"),
    "DK": ("DNK", "Denmark"),
    "DO": ("DOM", "Dominican Republic"),
    "DZ": ("DZA", "Algeria"),
    "EC": ("ECU", "Ecuador"),
    "EE": ("EST", "Estonia"),
    "EG": ("EGY", "Egypt"),
    "ES": ("ESP", "Spain"),
    "ET": ("ETH", "Ethiopia"),
    "FI": ("FIN", "Finland"),
    "FJ": ("FJI", "Fiji"),
    "FR": ("FRA", "France"),
    "GA": ("GAB", "Gabon"),
    "GB": ("GBR", "United Kingdom"),
    "GE": ("GEO", "Georgia"),
    "GH": ("GHA", "Ghana"),
    "GN": ("GIN", "Guinea"),
    "GP": ("GLP", "Guadeloupe"),
    "GQ": ("GNQ", "Equatorial Guinea"),
    "GR": ("GRC", "Greece"),
    "GT": ("GTM", "Guatemala"),
    "GU": ("GUM", "Guam"),
    "GY": ("GUY", "Guyana"),
    "HK": ("HKG", "Hong Kong"),
    "HN": ("HND", "Honduras"),
    "HR": ("HRV", "Croatia"),
    "HU": ("HUN", "Hungary"),
    "ID": ("IDN", "Indonesia"),
    "IE": ("IRL", "Ireland"),
    "IL": ("ISR", "Israel"),
    "IN": ("IND", "India"),
    "IQ": ("IRQ", "Iraq"),
    "IR": ("IRN", "Iran"),
    "IS": ("ISL", "Iceland"),
    "IT": ("ITA", "Italy"),
    "JM": ("JAM", "Jamaica"),
    "JO": ("JOR", "Jordan"),
    "JP": ("JPN", "Japan"),
    "KE": ("KEN", "Kenya"),
    "KG": ("KGZ", "Kyrgyzstan"),
    "KH": ("KHM", "Cambodia"),
    "KP": ("PRK", "North Korea"),
    "KR": ("KOR", "South Korea"),
    "KW": ("KWT", "Kuwait"),
    "KZ": ("KAZ", "Kazakhstan"),
    "LA": ("LAO", "Laos"),
    "LB": ("LBN", "Lebanon"),
    "LK": ("LKA", "Sri Lanka"),
    "LT": ("LTU", "Lithuania"),
    "LU": ("LUX", "Luxembourg"),
    "LV": ("LVA", "Latvia"),
    "LY": ("LBY", "Libya"),
    "MA": ("MAR", "Morocco"),
    "MD": ("MDA", "Moldova"),
    "ME": ("MNE", "Montenegro"),
    "MG": ("MDG", "Madagascar"),
    "MK": ("MKD", "North Macedonia"),
    "ML": ("MLI", "Mali"),
    "MM": ("MMR", "Myanmar"),
    "MN": ("MNG", "Mongolia"),
    "MO": ("MAC", "Macao"),
    "MQ": ("MTQ", "Martinique"),
    "MR": ("MRT", "Mauritania"),
    "MT": ("MLT", "Malta"),
    "MU": ("MUS", "Mauritius"),
    "MW": ("MWI", "Malawi"),
    "MX": ("MEX", "Mexico"),
    "MY": ("MYS", "Malaysia"),
    "MZ": ("MOZ", "Mozambique"),
    "NA": ("NAM", "Namibia"),
    "NC": ("NCL", "New Caledonia"),
    "NE": ("NER", "Niger"),
    "NG": ("NGA", "Nigeria"),
    "NI": ("NIC", "Nicaragua"),
    "NL": ("NLD", "Netherlands"),
    "NO": ("NOR", "Norway"),
    "NP": ("NPL", "Nepal"),
    "NZ": ("NZL", "New Zealand"),
    "OM": ("OMN", "Oman"),
    "PA": ("PAN", "Panama"),
    "PE": ("PER", "Peru"),
    "PG": ("PNG", "Papua New Guinea"),
    "PH": ("PHL", "Philippines"),
    "PK": ("PAK", "Pakistan"),
    "PL": ("POL", "Poland"),
    "PR": ("PRI", "Puerto Rico"),
    "PS": ("PSE", "Palestine"),
    "PT": ("PRT", "Portugal"),
    "PY": ("PRY", "Paraguay"),
    "QA": ("QAT", "Qatar"),
    "RE": ("REU", "Reunion"),
    "RO": ("ROU", "Romania"),
    "RS": ("SRB", "Serbia"),
    "RU": ("RUS", "Russia"),
    "RW": ("RWA", "Rwanda"),
    "SA": ("SAU", "Saudi Arabia"),
    "SD": ("SDN", "Sudan"),
    "SE": ("SWE", "Sweden"),
    "SG": ("SGP", "Singapore"),
    "SI": ("SVN", "Slovenia"),
    "SK": ("SVK", "Slovakia"),
    "SN": ("SEN", "Senegal"),
    "SR": ("SUR", "Suriname"),
    "SV": ("SLV", "El Salvador"),
    "SY": ("SYR", "Syria"),
    "SZ": ("SWZ", "Eswatini"),
    "TG": ("TGO", "Togo"),
    "TH": ("THA", "Thailand"),
    "TJ": ("TJK", "Tajikistan"),
    "TM": ("TKM", "Turkmenistan"),
    "TN": ("TUN", "Tunisia"),
    "TR": ("TUR", "Turkey"),
    "TT": ("TTO", "Trinidad and Tobago"),
    "TW": ("TWN", "Taiwan"),
    "TZ": ("TZA", "Tanzania"),
    "UA": ("UKR", "Ukraine"),
    "UG": ("UGA", "Uganda"),
    "US": ("USA", "United States"),
    "UY": ("URY", "Uruguay"),
    "UZ": ("UZB", "Uzbekistan"),
    "VE": ("VEN", "Venezuela"),
    "VN": ("VNM", "Vietnam"),
    "XK": ("XKX", "Kosovo"),
    "YE": ("YEM", "Yemen"),
    "ZA": ("ZAF", "South Africa"),
    "ZM": ("ZMB", "Zambia"),
    "ZW": ("ZWE", "Zimbabwe"),
}

# ── Ember "Fuel" → model generator bucket ───────────────────────────────────────
# The model has seven buckets. Hydro is first-class (Ember reports it separately, and it
# dominates entire grids — NO, BR, CA, PY, ...); "other" absorbs bioenergy and residual
# fossil, which the model treats as a dispatchable catch-all.
FUEL_TO_BUCKET: dict[str, str] = {
    "Solar": "solar",
    "Wind": "wind_onshore",
    "Gas": "gas_ccgt",
    "Coal": "coal",
    "Nuclear": "nuclear",
    "Hydro": "hydro",
    "Bioenergy": "other",
    "Other Fossil": "other",
    "Other Renewables": "other",  # geothermal etc. — without this, shares miss ~3% for TR/ID/IT/PH
}
BUCKETS = ["solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "hydro", "other"]

# ── Hydro bucket ─────────────────────────────────────────────────────────────────
# Costs: IRENA Renewable Power Generation Costs 2024 (global weighted-average hydropower:
# installed cost ~2,800 USD/kW, O&M ~2%/yr of capex, 60-yr economic life). Zero fuel, zero
# direct CO2. Dispatch: hydro bids into the merit order as a flexible zero-fuel, zero-emission
# unit (cheapest flexible ⇒ dispatched ahead of gas/coal). It is deliberately NOT given a flat
# per-hour availability ceiling: reservoir hydro peaks far above its average output (a flat cap
# at the annual CF strips that peaking and manufactures blackouts for reservoir-dominated grids —
# NO/PY LOLE would jump into the thousands of hours). This is the same free-dispatch treatment
# hydro already had while it lived inside the "other" bucket, so reliability behaviour is
# unchanged; only the cost, emissions and share attribution are corrected. ``cf_base`` is the
# real Ember capacity factor, used for capacity sizing when a run is share-based and for display —
# not as a hard hourly cap.
HYDRO_TEMPLATE_BLOCK: dict[str, Any] = {
    "capex_usd_kw": 2800,
    "opex_fixed_usd_kw_yr": 55,
    "opex_var_usd_mwh": 1.0,
    "lifetime_yr": 60,
    "emission_factor_tco2_mwh": 0.0,
    "fuel_usd_mmbtu": 0.0,
    "heat_rate_mmbtu_mwh": 0.0,
    "cf_base": 0.40,
    "cf_eff_func": {
        "type": "constant",
        "params": {"a": 0.40},
        "x_min": 0.1,
        "x_max": 0.65,
        "source": "Base CF from Ember generation / capacity",
    },
    "eta_func": {
        "type": "constant",
        "params": {"a": 0.9},
        "x_min": 0.85,
        "x_max": 0.95,
        "source": "Hydro turbine efficiency (no fuel conversion)",
    },
    "integration_cost_func": {
        "type": "constant",
        "params": {"a": 0.5},
        "source": "Dispatchable renewable — minimal system overhead",
    },
    # Mostly dispatchable; the nonzero factor reflects the run-of-river slice and seasonal
    # inflow the operator cannot schedule.
    "variability_factor": 0.2,
    "import_fuel_fraction": 0.0,
}
SOURCE_HYDRO = (
    "Hydro split out of 'other' into its own bucket: Ember capacity and generation; costs from "
    "IRENA Renewable Power Generation Costs 2024 (~2,800 USD/kW, ~2%/yr O&M, 60-yr life); "
    "dispatched as a flexible zero-fuel, zero-emission unit in the merit order (reservoir "
    "peaking preserved, no flat availability cap)"
)

# Within the "other" bucket only "Other Fossil" (oil/diesel steam and misc thermal) emits CO2 and
# burns imported fuel; hydro, geothermal and (by grid-accounting convention, as in Ember's own
# clean/fossil split) bioenergy do not. Each country's "other" block is therefore scaled by its
# *real* fossil fraction within the bucket — a flat template value would overstate emissions,
# fuel cost and import exposure for hydro/geothermal grids (KE, PY, NP, ...) and understate them
# for oil-fired ones (SA, KW, LB, ...).
EF_OTHER_FOSSIL_TCO2_MWH = 0.70  # oil/diesel steam turbine: IPCC 2006 oil EF at ~38% efficiency
OTHER_FUEL_BASE_USD_MMBTU = 5.0  # base "other" (oil/diesel) delivered fuel price, scaled by fossil share
MIN_OTHER_GEN_TWH = 0.05  # below this the fossil fraction is numerically meaningless → template

# ── Energy dependency: UN Comtrade net fuel imports → import_fuel_fraction ──────
# Produced by build_energy_dependency.py (net imported PJ of coal/gas/oil per country from the
# UN Comtrade preview API). Each generator's import_fuel_fraction is the share of its fuel burn
# met by net imports: f = clip(max(0, M − X) / burn, 0, 1), with burn = generation × heat rate.
# Assumes imported fuel is available to the power sector pro-rata up to its total burn; net
# exporters get 0. Countries without a usable Comtrade report fall back to fully-imported (the
# pre-Comtrade stylization) unless a sourced manual override says otherwise.
DEPENDENCY_JSON = DATA_DIR / "energy_dependency.json"
GJ_PER_MMBTU = 1.055056  # exact definition (1 MMBtu = 1.055056 GJ)
MIN_FUEL_BURN_PJ = 0.5   # below this the ratio is numerically meaningless → net-importer flag

# Physical capacity-factor bands. Ember gen÷cap is clipped into these so a country
# with a tiny/new fleet (division blow-ups) or a partial reporting year can't emit an
# absurd CF. Outside the band → clip; near-zero capacity → keep the template default.
CF_BOUNDS: dict[str, tuple[float, float]] = {
    "solar": (0.07, 0.30),
    "wind_onshore": (0.10, 0.55),
    "gas_ccgt": (0.10, 0.90),
    "coal": (0.10, 0.90),
    "nuclear": (0.40, 0.95),
    "hydro": (0.10, 0.65),  # global fleet CFs: ~15% (peaking/dry) to ~60% (wet tropics/Nordics)
    "other": (0.10, 0.85),
}
MIN_CAPACITY_GW = 0.20  # below this, CF is numerically unreliable → use template default

# ── Firm-generator availability ceiling (default max_cf) ──────────────────────────
# Each firm/dispatchable thermal generator gets a default maximum capacity factor equal to its
# real annual CF (Ember generation ÷ capacity) rounded UP to the next 10% mark — a "hold the plant
# near its historical utilization, plus one step of headroom" ceiling. The dispatch enforces it as
# an hourly power cap (capacity × max_cf) with a request override; renewables and hydro are NOT
# capped this way (a weather resource's annual mean is far below its hourly peak, and hydro is
# energy-limited — capping either at its annual CF throws away real output / manufactures blackouts).
# Firm plants with a low annual CF (backup/peakers) get a correspondingly low ceiling: this is the
# intended semantics — it reveals when a grid's reliability depends on running firm plant well above
# its historical utilization. Users see and can raise each value in the UI.
FIRM_GENERATORS = ("gas_ccgt", "coal", "nuclear", "other")
MAX_CF_STEP = 0.10  # ceiling granularity


def ceil_to_step(cf: float, step: float = MAX_CF_STEP) -> float:
    """Smallest multiple of ``step`` that is ≥ ``cf`` (a value already on a mark stays put)."""
    steps = math.ceil(round(cf, 4) / step - 1e-9)
    return round(min(1.0, max(step, steps * step)), 4)

# ── Cost / fuel / discount template ─────────────────────────────────────────────
# Technology *structure* (dispatch functions, heat rates, emission factors, storage, and the
# capex/opex base levels) comes from the shared literature template (TEMPLATE_COUNTRY's profile).
# On top of that, three cost levers that genuinely vary by market are applied per **region**:
# delivered fuel prices (gas/coal), the discount rate (WACC), and a VRE capex multiplier.

# Each country's region. Regions group markets with similar delivered fuel prices and cost of
# capital.
COUNTRY_REGION: dict[str, str] = {
    # africa
    "AO": "africa", "BF": "africa", "BJ": "africa", "BW": "africa", "CD": "africa",
    "CG": "africa", "CI": "africa", "CM": "africa", "DZ": "africa", "EG": "africa",
    "ET": "africa", "GA": "africa", "GH": "africa", "GN": "africa", "GQ": "africa",
    "KE": "africa", "LY": "africa", "MA": "africa", "MG": "africa", "ML": "africa",
    "MR": "africa", "MU": "africa", "MW": "africa", "MZ": "africa", "NA": "africa",
    "NE": "africa", "NG": "africa", "RE": "africa", "RW": "africa", "SD": "africa",
    "SN": "africa", "SZ": "africa", "TG": "africa", "TN": "africa", "TZ": "africa",
    "UG": "africa", "ZA": "africa", "ZM": "africa", "ZW": "africa",
    # central_asia
    "AZ": "central_asia", "KG": "central_asia", "KZ": "central_asia", "MN": "central_asia", "TJ": "central_asia",
    "TM": "central_asia", "UZ": "central_asia",
    # china
    "CN": "china", "KP": "china",
    # emerging_europe
    "AM": "emerging_europe", "BA": "emerging_europe", "BY": "emerging_europe", "GE": "emerging_europe", "MD": "emerging_europe",
    "ME": "emerging_europe", "MK": "emerging_europe", "RS": "emerging_europe", "RU": "emerging_europe", "TR": "emerging_europe",
    "UA": "emerging_europe", "XK": "emerging_europe",
    # europe
    "AT": "europe", "BE": "europe", "BG": "europe", "CH": "europe", "CY": "europe",
    "CZ": "europe", "DE": "europe", "DK": "europe", "EE": "europe", "ES": "europe",
    "FI": "europe", "FR": "europe", "GB": "europe", "GR": "europe", "HR": "europe",
    "HU": "europe", "IE": "europe", "IS": "europe", "IT": "europe", "LT": "europe",
    "LU": "europe", "LV": "europe", "MT": "europe", "NL": "europe", "NO": "europe",
    "PL": "europe", "PT": "europe", "RO": "europe", "SE": "europe", "SI": "europe",
    "SK": "europe",
    # lat_am
    "AR": "lat_am", "AW": "lat_am", "BB": "lat_am", "BO": "lat_am", "BR": "lat_am",
    "BS": "lat_am", "CL": "lat_am", "CO": "lat_am", "CR": "lat_am", "CU": "lat_am",
    "DO": "lat_am", "EC": "lat_am", "GP": "lat_am", "GT": "lat_am", "GY": "lat_am",
    "HN": "lat_am", "JM": "lat_am", "MQ": "lat_am", "NI": "lat_am", "PA": "lat_am",
    "PE": "lat_am", "PR": "lat_am", "PY": "lat_am", "SR": "lat_am", "SV": "lat_am",
    "TT": "lat_am", "UY": "lat_am", "VE": "lat_am",
    # middle_east
    "AE": "middle_east", "BH": "middle_east", "IL": "middle_east", "IQ": "middle_east", "IR": "middle_east",
    "JO": "middle_east", "KW": "middle_east", "LB": "middle_east", "OM": "middle_east", "PS": "middle_east",
    "QA": "middle_east", "SA": "middle_east", "SY": "middle_east", "YE": "middle_east",
    # ne_asia_adv
    "HK": "ne_asia_adv", "JP": "ne_asia_adv", "KR": "ne_asia_adv", "MO": "ne_asia_adv", "SG": "ne_asia_adv",
    "TW": "ne_asia_adv",
    # north_america
    "CA": "north_america", "MX": "north_america", "US": "north_america",
    # oceania
    "AU": "oceania", "NZ": "oceania",
    # se_asia
    "BN": "se_asia", "FJ": "se_asia", "GU": "se_asia", "ID": "se_asia", "KH": "se_asia",
    "LA": "se_asia", "MM": "se_asia", "MY": "se_asia", "NC": "se_asia", "PG": "se_asia",
    "PH": "se_asia", "TH": "se_asia", "VN": "se_asia",
    # south_asia
    "AF": "south_asia", "BD": "south_asia", "BT": "south_asia", "IN": "south_asia", "LK": "south_asia",
    "NP": "south_asia", "PK": "south_asia",
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
    "central_asia":   {"gas": 4.0,  "coal": 2.5, "wacc": 0.100, "vre_mult": 0.90},
    "africa":         {"gas": 6.0,  "coal": 2.0, "wacc": 0.100, "vre_mult": 1.00},
    "emerging_europe": {"gas": 11.0, "coal": 4.5, "wacc": 0.100, "vre_mult": 1.00},
}

# ── Offshore wind split ──────────────────────────────────────────────────────────
# Ember reports a single "Wind" figure. We carve offshore out of it using a curated
# offshore-capacity table (GWEC Global Wind Report 2024 / IRENA statistics, end-2024, GW), then
# attribute generation at an assumed offshore capacity factor and leave the remainder to onshore.
# Countries not listed have no material offshore fleet (offshore capacity 0).
OFFSHORE_CAPACITY_GW: dict[str, float] = {
    "CN": 38.0, "GB": 14.7, "DE": 8.5, "NL": 4.7, "DK": 2.7, "TW": 2.4, "BE": 2.3, "FR": 1.5,
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
    "hydro": {"up": 1.0, "down": 1.0},      # reservoir turbines ramp full range in minutes
    "other": {"up": 0.7, "down": 0.7},      # bioenergy/OCGT peaker mix: fairly flexible
}
SOURCE_RAMP = (
    "Per-technology ramp rates (fraction of nameplate per hour) stylized from IEA/NREL unit-"
    "flexibility literature; nuclear runs as flat baseload and VRE is weather-driven (no ramp limit)"
)
# Pumped-hydro storage tier (bulk, cheap-per-kWh, ~78% round-trip): IHA 2023 / IEA / NREL ATB
# ranges — installed energy cost ~65 USD/kWh, ~60-yr life, ~200 cycles/yr, 90% depth-of-discharge,
# ~10 h default duration. Distinct from batteries (short tier) and seasonal H2 (long tier).
PHS_ESS_BLOCK: dict[str, Any] = {
    "capex_usd_kwh": 65,
    "lifetime_yr": 60,
    "cycles_per_year": 200,
    "dod": 0.9,
    "duration_hr": 10,
    "round_trip_efficiency": 0.78,
}
SOURCE_PHS = (
    "Pumped-hydro storage tier (bulk, ~78% round-trip, ~65 USD/kWh, ~60-yr life): IHA 2023 / "
    "IEA / NREL ATB; distinct from the battery (short) and seasonal (long) tiers"
)
SOURCE_MAXCF = (
    "Default availability ceiling (max_cf) on firm generators (gas/coal/nuclear/other) = the real "
    "Ember annual capacity factor rounded up to the next 10%; renewables and hydro are uncapped. "
    "Enforced as an hourly power cap in dispatch, overridable per request/generator."
)

# ── Synthetic-profile knobs written into every profile as config (were hardcoded in the core) ────
# Sourced from the core fallback constants so the config and the code default can never diverge.
from backend.core.dispatch_engine import _ARBITRAGE_PRICE_PERCENTILE  # noqa: E402
from backend.core.hourly_profiles import (  # noqa: E402
    _VRE_DROUGHT_EVENTS,
    _VRE_DROUGHT_MAX_HR,
    _VRE_DROUGHT_MIN_HR,
    _VRE_DROUGHT_SOLAR_FLOOR,
    _VRE_DROUGHT_WIND_FLOOR,
    _WIND_AR1_RHO,
)

VRE_DROUGHT_DEFAULTS: dict[str, float] = {
    "events": _VRE_DROUGHT_EVENTS,               # winter Dunkelflaute events injected per synthetic year
    "min_duration_hr": _VRE_DROUGHT_MIN_HR,      # shortest event (h)
    "max_duration_hr": _VRE_DROUGHT_MAX_HR,      # longest event (h) — sets storage-energy need
    "wind_floor": _VRE_DROUGHT_WIND_FLOOR,       # wind-shape multiplier at the trough core (deep calm)
    "solar_floor": _VRE_DROUGHT_SOLAR_FLOOR,     # solar-shape multiplier at the trough core (overcast)
}
SOURCE_SYNTHESIS = (
    "Synthetic-profile stress knobs (VRE-drought frequency/duration/depth, wind AR(1) persistence, "
    "short-storage arbitrage price-percentile) — stylized reliability-modelling defaults, editable "
    "per country"
)

SOURCE_OFFSHORE = (
    "Offshore wind capacity split from GWEC Global Wind Report 2024 / IRENA statistics; offshore "
    "costs from IRENA 2024 / NREL ATB 2024"
)
SOURCE_OTHER = (
    "'Other' bucket (bioenergy/geothermal/other fossil) emission factor, fuel cost and "
    "import-fuel weighting scaled by the bucket's real fossil share from Ember generation by "
    "fuel; oil/diesel steam EF 0.70 tCO2/MWh (IPCC 2006 at ~38% efficiency)"
)
SOURCE_DEPENDENCY = (
    "Energy dependency (import_fuel_fraction per generator) from UN Comtrade net fuel imports "
    "(coal HS 2701, oil HS 2709+2710, natural gas HS 271111+271121; net weight x IPCC 2006 "
    "NCVs) vs the power sector's Ember-derived fuel burn; net exporters count as 0"
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
    other_fossil: dict[int, float] = defaultdict(float)

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
            if var == "Other Fossil":  # the only emitting / imported-fuel slice of "other"
                other_fossil[year] += val

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
        "other_fossil_twh": other_fossil.get(year, 0.0),
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
                  data: dict[str, Any], dependency: dict[str, Any] | None = None) -> dict[str, Any]:
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
    # Latitude (config) — the synthetic-profile seasonal/drought logic derives the hemisphere from
    # this, so southern-hemisphere countries get their winter in the right half of the year.
    from backend.data.build_hourly_profiles import COORDS
    if code in COORDS:
        profile["latitude"] = COORDS[code][0]
    profile["generators"]["gas_ccgt"]["fuel_usd_mmbtu"] = region["gas"]
    profile["generators"]["coal"]["fuel_usd_mmbtu"] = region["coal"]
    for vre in ("solar", "wind_onshore"):
        base_capex = float(template["generators"][vre]["capex_usd_kw"])
        profile["generators"][vre]["capex_usd_kw"] = round(base_capex * region["vre_mult"], 1)

    # Hydro block: always stamped fresh from the literature template so a stale block in the
    # structural template (KR.json is both a profile and the template) can never leak one
    # country's water budget into another's.
    profile["generators"]["hydro"] = json.loads(json.dumps(HYDRO_TEMPLATE_BLOCK))

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

    # Anchor the hydro CF-display function at the country's real Ember CF (see HYDRO_TEMPLATE_BLOCK
    # for why hydro carries no flat availability cap).
    hydro_block = profile["generators"]["hydro"]
    hydro_block["cf_eff_func"]["params"]["a"] = hydro_block["cf_base"]

    # Default availability ceiling on firm generators: real annual CF rounded up to the next 10%
    # (see FIRM_GENERATORS / ceil_to_step). Renewables and hydro are left uncapped. Pop first so a
    # generator the country lacks (near-zero capacity) never keeps a max_cf inherited from the
    # structural template via the deep copy.
    for gen in FIRM_GENERATORS:
        block = profile["generators"].get(gen)
        if block is None:
            continue
        block.pop("max_cf", None)
        if capacities.get(gen, 0.0) >= MIN_CAPACITY_GW:
            block["max_cf"] = ceil_to_step(float(block["cf_base"]))

    _split_offshore_wind(code, profile, template, capacities, shares, data, total_gen, region)
    _scale_other_bucket(profile, data)
    _apply_import_fractions(code, profile, data, dependency or _load_energy_dependency())

    # Config-backed per-technology ramp rates onto the flexible-thermal blocks (editable in the UI).
    for tech, rates in RAMP_DEFAULTS.items():
        if tech in profile["generators"]:
            profile["generators"][tech]["ramp_up_frac_per_hr"] = rates["up"]
            profile["generators"][tech]["ramp_down_frac_per_hr"] = rates["down"]

    # Config-backed synthetic-profile stress knobs (were hardcoded in the core synthesis/dispatch).
    profile["vre_drought"] = dict(VRE_DROUGHT_DEFAULTS)
    if "wind_onshore" in profile["generators"]:
        profile["generators"]["wind_onshore"]["wind_ar1_rho"] = _WIND_AR1_RHO
    if "short_dur" in profile.get("ess", {}):
        profile["ess"]["short_dur"]["arbitrage_price_percentile"] = _ARBITRAGE_PRICE_PERCENTILE
    # Pumped-hydro storage tier economics (stamped in code so it can't drift; power/duration are
    # user inputs at request time, defaulting to 0 = no PHS).
    profile.setdefault("ess", {})["phs_dur"] = dict(PHS_ESS_BLOCK)

    profile["capacities_gw"] = capacities
    profile["shares"] = shares
    profile["sources"] = [
        f"{SOURCE_EMBER}; data year {data['year']}",
        SOURCE_COSTS,
        SOURCE_REGIONAL,
        SOURCE_OFFSHORE,
        SOURCE_HYDRO,
        SOURCE_OTHER,
        SOURCE_DEPENDENCY,
        SOURCE_RAMP,
        SOURCE_MAXCF,
        SOURCE_PHS,
        SOURCE_SYNTHESIS,
    ]
    return profile


def _scale_other_bucket(profile: dict[str, Any], data: dict[str, Any]) -> None:
    """Scale the "other" bucket's emissions, fuel cost and import exposure by its fossil share.

    Algorithm:
        $$f = G_{other\\ fossil} / G_{other}$$
        $$EF_{other} = 0.70 \\cdot f \\quad [tCO_2/MWh], \\qquad
          p_{fuel,other} = p_{base} \\cdot f \\quad [USD/MMBtu],\\ p_{base}=5.0$$
    ASCII: f = other-fossil TWh / other TWh; EF = 0.70*f; fuel price = 5.0*f (fixed base, so the
    scaling is idempotent across rebuilds); the imported-fuel weighting also scales by f.

    ``G`` are Ember generation figures (TWh) for the data year. Geothermal and (by
    grid-accounting convention) bioenergy are carbon-free and domestic, so only the "Other
    Fossil" slice (oil/diesel steam, EF 0.70 tCO2/MWh at ~38% efficiency) emits, pays for fuel
    and counts toward import dependency. Hydro has its own bucket and is not part of "other".
    ``import_fuel_fraction`` is read by the LCOE engine's energy-security metric. Buckets with
    < ``MIN_OTHER_GEN_TWH`` keep the stylized template block.
    """
    other_gen_twh = data["generation_twh"]["other"]
    if other_gen_twh < MIN_OTHER_GEN_TWH:
        return
    f = min(max(data["other_fossil_twh"] / other_gen_twh, 0.0), 1.0)
    gen_block = profile["generators"]["other"]
    gen_block["emission_factor_tco2_mwh"] = round(EF_OTHER_FOSSIL_TCO2_MWH * f, 4)
    # Scale from the fixed base price, NOT the block's current value — the structural template
    # (KR.json) is itself a built profile whose "other" fuel is already scaled, so reading the
    # block and re-multiplying compounds the value toward zero on every rebuild (non-idempotent).
    gen_block["fuel_usd_mmbtu"] = round(OTHER_FUEL_BASE_USD_MMBTU * f, 4)
    gen_block["fossil_fraction"] = round(f, 4)
    # Import exposure = fossil slice × how much of that oil is imported (Comtrade); refined in
    # _apply_import_fractions once the country's net-import data is known.
    gen_block["import_fuel_fraction"] = round(f, 4)


def _load_energy_dependency() -> dict[str, Any]:
    if DEPENDENCY_JSON.exists():
        return json.loads(DEPENDENCY_JSON.read_text())
    return {"countries": {}, "manual_overrides": {}, "missing": []}


def _import_fraction(net_pj: float, burn_pj: float) -> float:
    """Share of a fuel burn met by net imports.

    Algorithm:
        $$f = \\mathrm{clip}\\!\\left(\\frac{\\max(0, M - X)}{B}, 0, 1\\right)$$
    ASCII: f = clip(max(0, net imports PJ) / burn PJ, 0, 1).

    ``M − X`` = net imported energy (PJ, from Comtrade); ``B`` = the power sector's fuel burn
    (PJ). Net exporters → 0. When the burn is too small for the ratio to mean anything the
    country's net-trade sign decides (importer → 1, else 0), so a generator added later in the
    UI still carries the right exposure.
    """
    if burn_pj < MIN_FUEL_BURN_PJ:
        return 1.0 if net_pj > 0.0 else 0.0
    return round(min(max(net_pj, 0.0) / burn_pj, 1.0), 4)


def _apply_import_fractions(code: str, profile: dict[str, Any], data: dict[str, Any],
                            dependency: dict[str, Any]) -> None:
    """Stamp Comtrade-derived ``import_fuel_fraction`` onto gas, coal and the other bucket."""
    gens = profile["generators"]
    entry = dependency["countries"].get(code)
    overrides = dependency.get("manual_overrides", {}).get(code, {})
    if entry is None and not overrides:
        return  # no Comtrade report → keep the fully-imported default (documented fallback)
    net = (entry or {}).get("net_imports_pj", {})

    burns = {
        "gas": data["generation_twh"]["gas_ccgt"]
        * float(gens["gas_ccgt"]["heat_rate_mmbtu_mwh"]) * GJ_PER_MMBTU,
        "coal": data["generation_twh"]["coal"]
        * float(gens["coal"]["heat_rate_mmbtu_mwh"]) * GJ_PER_MMBTU,
        "oil": data["other_fossil_twh"]
        * float(gens["other"]["heat_rate_mmbtu_mwh"]) * GJ_PER_MMBTU,
    }
    fractions = {
        fuel: overrides.get(fuel, _import_fraction(float(net.get(fuel, 0.0)), burns[fuel]))
        if (entry is not None or fuel in overrides) else 1.0
        for fuel in ("gas", "coal", "oil")
    }
    gens["gas_ccgt"]["import_fuel_fraction"] = fractions["gas"]
    gens["coal"]["import_fuel_fraction"] = fractions["coal"]
    fossil_fraction = float(gens["other"].get("fossil_fraction",
                                              gens["other"].get("import_fuel_fraction", 1.0)))
    gens["other"]["import_fuel_fraction"] = round(fossil_fraction * fractions["oil"], 4)
    profile["energy_dependency_year"] = (entry or {}).get("year")


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
    dependency = _load_energy_dependency()
    if not dependency["countries"]:
        print("note: energy_dependency.json missing — import fractions default to 1.0 "
              "(run build_energy_dependency first)", file=sys.stderr)
    year_cap = latest_settled_year(rows)
    print(f"Latest settled Ember year (provisional years excluded): {year_cap}\n")

    print(f"{'code':<5}{'yr':<6}{'demand':>9}{'solar_cf':>10}{'wind_cf':>9}"
          f"{'solarGW':>9}{'windGW':>8}{'gasGW':>8}{'coalGW':>8}{'nucGW':>8}")
    written = 0
    manifest_countries: dict[str, dict[str, Any]] = {}
    for code, (iso3, name) in sorted(COUNTRIES.items()):
        data = extract_country(rows, iso3, year_cap)
        profile = build_profile(code, iso3, name, template, data, dependency)
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
