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
    "AE": (24.45, 54.38), "AR": (-34.61, -58.38), "AU": (-33.87, 151.21), "BR": (-23.55, -46.63),
    "CA": (43.65, -79.38), "CL": (-33.45, -70.67), "CN": (34.27, 108.93), "DE": (52.52, 13.40),
    "DK": (55.68, 12.57), "ES": (40.42, -3.70), "FI": (60.17, 24.94), "FR": (48.85, 2.35),
    "GB": (51.51, -0.13), "ID": (-6.21, 106.85), "IE": (53.35, -6.26), "IN": (28.61, 77.21),
    "IT": (41.90, 12.50), "JP": (35.68, 139.69), "KR": (37.57, 126.98), "MX": (19.43, -99.13),
    "MY": (3.14, 101.69), "NL": (52.37, 4.90), "NO": (59.91, 10.75), "PH": (14.60, 120.98),
    "PL": (52.23, 21.01), "SA": (24.71, 46.68), "SE": (59.33, 18.06), "TH": (13.76, 100.50),
    "TR": (39.93, 32.86), "TW": (25.03, 121.57), "US": (37.00, -96.00), "VN": (21.03, 105.85),
    "ZA": (-26.20, 28.05),
}

# Standard UTC offset (hours) at each load centre. PVGIS timestamps are UTC; the model indexes
# hours as *local* time (its synthesizer peaks solar at local noon, demand in the local evening),
# so the PVGIS series is rolled by this offset to align midday with the local-noon index.
UTC_OFFSET: dict[str, int] = {
    "AE": 4, "AR": -3, "AU": 10, "BR": -3, "CA": -5, "CL": -4, "CN": 8, "DE": 1, "DK": 1,
    "ES": 1, "FI": 2, "FR": 1, "GB": 0, "ID": 7, "IE": 0, "IN": 6, "IT": 1, "JP": 9, "KR": 9,
    "MX": -6, "MY": 8, "NL": 1, "NO": 1, "PH": 8, "PL": 1, "SA": 3, "SE": 1, "TH": 7, "TR": 3,
    "TW": 8, "US": -6, "VN": 7, "ZA": 2,
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
