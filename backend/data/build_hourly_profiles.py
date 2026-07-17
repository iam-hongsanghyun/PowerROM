"""Build real hourly weather-year profiles for the dispatch model's ``data`` mode.

Writes ``backend/data/hourly/<CC>/<year>.csv.gz`` with three 8760-hour columns —
``demand_norm`` (load shape, mean 1), ``solar_cf`` and ``wind_cf`` (capacity factors in
[0, 1]) — which ``backend/core/hourly_profiles.py`` loads directly.

Coverage tiers (best data available per country):

* **Solar, every country** — real hourly PV capacity factor from **PVGIS** (EU JRC,
  SARAH/ERA5 reanalysis) at the country's load-centre coordinates. No token.
* **Load + wind, EU + US** — real observed hourly series (added in a later pass from OPSD /
  EIA); until then load and wind use the physics-based synthesizer.
* **Load + wind, elsewhere** — the parametric synthesizer, calibrated to the country's real
  Ember annual capacity factor.

Every series is mean-scaled so its annual average equals the profile's Ember-derived
``cf_base`` (solar/wind) or 1.0 (demand), so switching a country from synthetic to real
weather changes the *shape* of a year, never its annual energy.

Run:
    python -m backend.data.build_hourly_profiles                 # all countries
    python -m backend.data.build_hourly_profiles --countries KR DE US
"""

from __future__ import annotations

import argparse
import gzip
import io
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from backend.core.hourly_profiles import HOURS_PER_YEAR, synthesize_parametric
from backend.core.lcoe_engine import load_country_profile

HOURLY_DIR = Path(__file__).resolve().parent / "hourly"
# Three non-leap weather years (8760 h each) within PVGIS-ERA5's 2005–2020 range, so the ensemble
# sampler has real inter-annual variability to bootstrap (matching the synthetic path's multi-year).
WEATHER_YEARS = (2017, 2018, 2019)
PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc"
PVGIS_THROTTLE_S = 1.2  # spacing between PVGIS calls — bursts of ~100 requests get rate-limited (400)

# Representative load-centre coordinates (lat, lon) per country — a national proxy for the
# solar diurnal/seasonal shape, which is driven mostly by latitude.
COORDS: dict[str, tuple[float, float]] = {
    "AE": (24.45, 54.38), "AF": (34.53, 69.17), "AM": (40.18, 44.51), "AO": (-8.84, 13.23),
    "AR": (-34.61, -58.38), "AT": (48.21, 16.37), "AU": (-33.87, 151.21), "AW": (12.52, -70.04),
    "AZ": (40.41, 49.87), "BA": (43.86, 18.41), "BB": (13.10, -59.62), "BD": (23.81, 90.41),
    "BE": (50.85, 4.35), "BF": (12.37, -1.52), "BG": (42.70, 23.32), "BH": (26.23, 50.59),
    "BJ": (6.37, 2.43), "BN": (4.94, 114.95), "BO": (-16.49, -68.15), "BR": (-23.55, -46.63),
    "BS": (25.05, -77.36), "BT": (27.47, 89.64), "BW": (-24.65, 25.91), "BY": (53.90, 27.57),
    "CA": (43.65, -79.38), "CD": (-4.44, 15.27), "CG": (-4.27, 15.28), "CH": (47.38, 8.54),
    "CI": (5.36, -4.01), "CL": (-33.45, -70.67), "CM": (4.05, 9.70), "CN": (34.27, 108.93),
    "CO": (4.71, -74.07), "CR": (9.93, -84.08), "CU": (23.11, -82.37), "CY": (35.19, 33.38),
    "CZ": (50.08, 14.44), "DE": (52.52, 13.40), "DK": (55.68, 12.57), "DO": (18.49, -69.93),
    "DZ": (36.75, 3.06), "EC": (-0.18, -78.47), "EE": (59.44, 24.75), "EG": (30.04, 31.24),
    "ES": (40.42, -3.70), "ET": (9.02, 38.75), "FI": (60.17, 24.94), "FJ": (-18.14, 178.44),
    "FR": (48.85, 2.35), "GA": (0.39, 9.45), "GB": (51.51, -0.13), "GE": (41.72, 44.83),
    "GH": (5.60, -0.19), "GN": (10.06, -12.86), "GP": (16.24, -61.53), "GQ": (3.75, 8.78),
    "GR": (37.98, 23.73), "GT": (14.63, -90.51), "GU": (13.44, 144.79), "GY": (6.80, -58.16),
    "HK": (22.32, 114.17), "HN": (14.07, -87.19), "HR": (45.81, 15.98), "HU": (47.50, 19.04),
    "ID": (-6.21, 106.85), "IE": (53.35, -6.26), "IL": (32.09, 34.78), "IN": (28.61, 77.21),
    "IQ": (33.31, 44.37), "IR": (35.69, 51.39), "IS": (64.15, -21.94), "IT": (41.90, 12.50),
    "JM": (17.97, -76.79), "JO": (31.95, 35.93), "JP": (35.68, 139.69), "KE": (-1.29, 36.82),
    "KG": (42.87, 74.59), "KH": (11.56, 104.92), "KP": (39.03, 125.75), "KR": (37.57, 126.98),
    "KW": (29.38, 47.99), "KZ": (43.24, 76.89), "LA": (17.97, 102.60), "LB": (33.89, 35.50),
    "LK": (6.93, 79.85), "LT": (54.69, 25.28), "LU": (49.61, 6.13), "LV": (56.95, 24.11),
    "LY": (32.89, 13.19), "MA": (33.57, -7.59), "MD": (47.01, 28.86), "ME": (42.44, 19.26),
    "MG": (-18.88, 47.51), "MK": (41.99, 21.43), "ML": (12.64, -8.00), "MM": (16.87, 96.20),
    "MN": (47.89, 106.91), "MO": (22.20, 113.55), "MQ": (14.61, -61.07), "MR": (18.08, -15.98),
    "MT": (35.90, 14.51), "MU": (-20.16, 57.50), "MW": (-13.96, 33.77), "MX": (19.43, -99.13),
    "MY": (3.14, 101.69), "MZ": (-25.97, 32.57), "NA": (-22.56, 17.08), "NC": (-21.55, 165.50),
    "NE": (13.51, 2.13), "NG": (6.52, 3.38), "NI": (12.11, -86.24), "NL": (52.37, 4.90),
    "NO": (59.91, 10.75), "NP": (27.72, 85.32), "NZ": (-36.85, 174.76), "OM": (23.59, 58.41),
    "PA": (8.98, -79.52), "PE": (-12.05, -77.04), "PG": (-9.44, 147.18), "PH": (14.60, 120.98),
    "PK": (24.86, 67.01), "PL": (52.23, 21.01), "PR": (18.22, -66.40), "PS": (31.90, 35.20),
    "PT": (38.72, -9.14), "PY": (-25.26, -57.58), "QA": (25.29, 51.53), "RE": (-20.88, 55.45),
    "RO": (44.43, 26.10), "RS": (44.79, 20.45), "RU": (55.75, 37.62), "RW": (-1.94, 30.06),
    "SA": (24.71, 46.68), "SD": (15.50, 32.56), "SE": (59.33, 18.06), "SG": (1.35, 103.82),
    "SI": (46.06, 14.51), "SK": (48.15, 17.11), "SN": (14.72, -17.47), "SR": (5.85, -55.20),
    "SV": (13.69, -89.19), "SY": (33.51, 36.29), "SZ": (-26.32, 31.13), "TG": (6.13, 1.22),
    "TH": (13.76, 100.50), "TJ": (38.56, 68.79), "TM": (37.96, 58.33), "TN": (36.81, 10.17),
    "TR": (39.93, 32.86), "TT": (10.65, -61.50), "TW": (25.03, 121.57), "TZ": (-6.79, 39.21),
    "UA": (50.45, 30.52), "UG": (0.35, 32.58), "US": (37.00, -96.00), "UY": (-34.90, -56.16),
    "UZ": (41.31, 69.24), "VE": (10.49, -66.88), "VN": (21.03, 105.85), "XK": (42.66, 21.17),
    "YE": (15.37, 44.19), "ZA": (-26.20, 28.05), "ZM": (-15.39, 28.32), "ZW": (-17.83, 31.05),
}

# Standard UTC offset (hours) at each load centre. PVGIS timestamps are UTC; the model indexes
# hours as *local* time (its synthesizer peaks solar at local noon, demand in the local evening),
# so the PVGIS series is rolled by this offset to align midday with the local-noon index.
UTC_OFFSET: dict[str, int] = {
    "AE": 4, "AF": 4, "AM": 4, "AO": 1, "AR": -3, "AT": 1, "AU": 10, "AW": -4, "AZ": 4,
    "BA": 1, "BB": -4, "BD": 6, "BE": 1, "BF": 0, "BG": 2, "BH": 3, "BJ": 1, "BN": 8,
    "BO": -4, "BR": -3, "BS": -5, "BT": 6, "BW": 2, "BY": 3, "CA": -5, "CD": 1, "CG": 1,
    "CH": 1, "CI": 0, "CL": -4, "CM": 1, "CN": 8, "CO": -5, "CR": -6, "CU": -5, "CY": 2,
    "CZ": 1, "DE": 1, "DK": 1, "DO": -4, "DZ": 1, "EC": -5, "EE": 2, "EG": 2, "ES": 1,
    "ET": 3, "FI": 2, "FJ": 12, "FR": 1, "GA": 1, "GB": 0, "GE": 4, "GH": 0, "GN": 0,
    "GP": -4, "GQ": 1, "GR": 2, "GT": -6, "GU": 10, "GY": -4, "HK": 8, "HN": -6, "HR": 1,
    "HU": 1, "ID": 7, "IE": 0, "IL": 2, "IN": 6, "IQ": 3, "IR": 4, "IS": 0, "IT": 1,
    "JM": -5, "JO": 3, "JP": 9, "KE": 3, "KG": 6, "KH": 7, "KP": 9, "KR": 9, "KW": 3,
    "KZ": 5, "LA": 7, "LB": 2, "LK": 6, "LT": 2, "LU": 1, "LV": 2, "LY": 2, "MA": 1,
    "MD": 2, "ME": 1, "MG": 3, "MK": 1, "ML": 0, "MM": 6, "MN": 8, "MO": 8, "MQ": -4,
    "MR": 0, "MT": 1, "MU": 4, "MW": 2, "MX": -6, "MY": 8, "MZ": 2, "NA": 2, "NC": 11,
    "NE": 1, "NG": 1, "NI": -6, "NL": 1, "NO": 1, "NP": 6, "NZ": 12, "OM": 4, "PA": -5,
    "PE": -5, "PG": 10, "PH": 8, "PK": 5, "PL": 1, "PR": -4, "PS": 2, "PT": 0, "PY": -4,
    "QA": 3, "RE": 4, "RO": 2, "RS": 1, "RU": 3, "RW": 2, "SA": 3, "SD": 2, "SE": 1,
    "SG": 8, "SI": 1, "SK": 1, "SN": 0, "SR": -3, "SV": -6, "SY": 3, "SZ": 2, "TG": 0,
    "TH": 7, "TJ": 5, "TM": 5, "TN": 1, "TR": 3, "TT": -4, "TW": 8, "TZ": 3, "UA": 2,
    "UG": 3, "US": -6, "UY": -3, "UZ": 5, "VE": -4, "VN": 7, "XK": 1, "YE": 3, "ZA": 2,
    "ZM": 2, "ZW": 2,
}


def _mean_scale(shape: np.ndarray, target_mean: float, upper: float) -> np.ndarray:
    """Scale ``shape`` so its mean equals ``target_mean`` while respecting the ``[0, upper]`` cap.

    A single scale-then-clip loses energy for high-CF countries (peaks hit the cap), dropping the
    mean below target. Iterating — rescale, clip, repeat — redistributes the clipped energy onto
    the unsaturated hours so the annual mean converges to ``target_mean`` (as long as it is
    physically reachable, i.e. below ``upper``).
    """
    x = np.asarray(shape, dtype=float)
    for _ in range(8):
        mean = float(np.mean(x))
        if mean <= 0:
            return np.zeros_like(x)
        x = np.clip(x * (target_mean / mean), 0.0, upper)
        if abs(float(np.mean(x)) - target_mean) < 1e-4:
            break
    return x


def fetch_pvgis_solar_cf(lat: float, lon: float, year: int) -> np.ndarray:
    """Hourly PV capacity factor (0–1) for a 1 kWp system at ``(lat, lon)`` from PVGIS.

    Returns exactly ``HOURS_PER_YEAR`` values (leap years are trimmed to 8760).
    """
    query = urllib.parse.urlencode({
        "lat": lat, "lon": lon, "startyear": year, "endyear": year,
        "pvcalculation": 1, "peakpower": 1, "loss": 14, "outputformat": "csv",
        "raddatabase": "PVGIS-ERA5",  # global coverage (SARAH is EU/Africa/Asia only)
    })
    text = ""
    for attempt in range(4):
        time.sleep(PVGIS_THROTTLE_S * (attempt + 1))  # throttle + exponential backoff on retry
        try:
            with urllib.request.urlopen(f"{PVGIS_URL}?{query}", timeout=90) as response:  # noqa: S310
                text = response.read().decode("utf-8", errors="replace")
            break
        except Exception:  # noqa: BLE001 — transient PVGIS hiccup / rate limit; back off and retry
            if attempt == 3:
                raise

    values: list[float] = []
    for line in text.splitlines():
        # Data rows look like "20190101:0010,83.64,...": timestamp then P (W) in column 2.
        if len(line) > 9 and line[:8].isdigit() and line[8] == ":":
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    values.append(float(parts[1]) / 1000.0)  # W per 1 kWp → capacity factor
                except ValueError:
                    continue
    cf = np.asarray(values, dtype=float)
    if cf.size < HOURS_PER_YEAR:
        raise ValueError(f"PVGIS returned only {cf.size} hours for ({lat},{lon},{year})")
    return cf[:HOURS_PER_YEAR]


def _write_year(code: str, year: int, profile: dict, lat: float, lon: float) -> dict[str, float]:
    solar_cf_base = float(profile["generators"]["solar"]["cf_base"])
    wind_cf_base = float(profile["generators"]["wind_onshore"]["cf_base"])

    # Real solar shape from PVGIS (UTC), rolled to local time, scaled to the Ember annual solar CF.
    solar_shape = np.roll(fetch_pvgis_solar_cf(lat, lon, year), UTC_OFFSET[code])
    solar_cf = _mean_scale(solar_shape, solar_cf_base, upper=0.96)

    # Demand + wind from the calibrated synthesizer (mean-scaled to cf_base / 1.0). A per-year
    # seed gives each weather year a distinct load/wind realisation for the ensemble.
    synth = synthesize_parametric(code, profile, seed=42 + year, year=year)
    demand_norm, wind_cf = synth.demand_norm, synth.wind_cf

    out_dir = HOURLY_DIR / code
    out_dir.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    buffer.write("demand_norm,solar_cf,wind_cf\n")
    for d, s, w in zip(demand_norm, solar_cf, wind_cf):
        buffer.write(f"{d:.5f},{s:.5f},{w:.5f}\n")
    (out_dir / f"{year}.csv.gz").write_bytes(gzip.compress(buffer.getvalue().encode("utf-8")))
    return {"solar_mean": float(np.mean(solar_cf)), "solar_base": solar_cf_base,
            "wind_mean": float(np.mean(wind_cf)), "wind_base": wind_cf_base}


def build_country(code: str, years: tuple[int, ...] = WEATHER_YEARS) -> dict[str, float]:
    """Build and write ``hourly/<code>/<year>.csv.gz`` for each weather year; return last summary."""
    profile = load_country_profile(code)
    lat, lon = COORDS[code]
    summary: dict[str, float] = {}
    for year in years:
        summary = _write_year(code, year, profile, lat, lon)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", nargs="*", help="subset of 2-letter codes (default: all)")
    parser.add_argument("--years", nargs="*", type=int, default=list(WEATHER_YEARS))
    args = parser.parse_args()

    codes = [c.upper() for c in (args.countries or sorted(COORDS))]
    years = tuple(args.years)
    print(f"Weather years: {years}")
    print(f"{'code':<5}{'solar_mean':>11}{'solar_base':>11}{'wind_mean':>10}{'wind_base':>10}  source")
    ok = 0
    for code in codes:
        if code not in COORDS:
            print(f"{code:<5}  no coordinates — skipped", file=sys.stderr)
            continue
        try:
            s = build_country(code, years=years)
            ok += 1
            print(f"{code:<5}{s['solar_mean']:>11.4f}{s['solar_base']:>11.4f}"
                  f"{s['wind_mean']:>10.4f}{s['wind_base']:>10.4f}  PVGIS solar + synth load/wind")
        except Exception as exc:  # noqa: BLE001 — report and continue the batch
            print(f"{code:<5}  ERROR: {exc}", file=sys.stderr)
    print(f"\nBuilt {ok}/{len(codes)} countries × {len(years)} years into {HOURLY_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
