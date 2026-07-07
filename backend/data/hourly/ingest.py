"""Ingest real hourly country profiles for the ``data`` dispatch mode.

PowerROM's ``dispatch_mode="data"`` loads per-country/year CSVs from
``backend/data/hourly/{COUNTRY}/{year}.csv`` with exactly three columns and 8760
rows (see ``backend.core.hourly_profiles._load_data_profiles``):

    demand_norm, solar_cf, wind_cf

* ``demand_norm`` — hourly electricity demand (any positive unit; the loader
  renormalises it to mean 1.0, so absolute scale does not matter).
* ``solar_cf`` / ``wind_cf`` — hourly capacity factors in [0, 1].

This module turns raw source data into that format. The two data pulls need free
API tokens, which you supply at runtime:

* **Solar/wind capacity factors** — `Renewables.ninja <https://renewables.ninja>`_
  (MERRA-2 / ERA5 reanalysis). Register for a free API token.
* **Electricity demand** — varies by region: ENTSO-E Transparency (EU), EIA (US),
  OpenNEM (AU), KPX (KR). Pass demand as a one-column hourly CSV via ``--demand-csv``
  (this module also ships an ENTSO-E helper for EU bidding zones).

Example:

    python -m backend.data.hourly.ingest \\
        --country KR --year 2023 --lat 37.5 --lon 127.0 \\
        --ninja-token "$NINJA_TOKEN" --demand-csv kr_2023_load_mw.csv

The absence of any file simply means ``data`` mode falls back to the parametric
synthesizer, so the tool keeps working while profiles are being sourced.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HOURS_PER_YEAR = 8760
HOURLY_DATA_DIR = Path(__file__).resolve().parent
REQUIRED_COLUMNS = ("demand_norm", "solar_cf", "wind_cf")
RENEWABLES_NINJA_BASE = "https://www.renewables.ninja/api"


def validate_profile_frame(frame: pd.DataFrame) -> None:
    """Raise if ``frame`` is not a valid hourly profile for the ``data`` loader."""
    missing = set(REQUIRED_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"Profile is missing columns: {sorted(missing)}")
    if len(frame) != HOURS_PER_YEAR:
        raise ValueError(f"Profile must have exactly {HOURS_PER_YEAR} rows, got {len(frame)}")
    if (frame["demand_norm"] <= 0).any():
        raise ValueError("demand_norm values must be positive")
    for column in ("solar_cf", "wind_cf"):
        values = frame[column].to_numpy()
        if values.min() < -1e-6 or values.max() > 1.0 + 1e-6:
            raise ValueError(f"{column} must lie within [0, 1]")


def write_profile_csv(
    country: str,
    year: int,
    demand: np.ndarray,
    solar_cf: np.ndarray,
    wind_cf: np.ndarray,
) -> Path:
    """Assemble, validate, and write a ``{COUNTRY}/{year}.csv`` profile.

    Args:
        country: ISO-like country code (folder name, upper-cased).
        year: Calendar year (file stem).
        demand: Hourly demand, any positive unit (renormalised on load).
        solar_cf / wind_cf: Hourly capacity factors in [0, 1].

    Returns:
        The path written.
    """
    frame = pd.DataFrame(
        {
            "demand_norm": np.asarray(demand, dtype=float),
            "solar_cf": np.clip(np.asarray(solar_cf, dtype=float), 0.0, 1.0),
            "wind_cf": np.clip(np.asarray(wind_cf, dtype=float), 0.0, 1.0),
        }
    )
    validate_profile_frame(frame)
    out_dir = HOURLY_DATA_DIR / country.upper()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{int(year)}.csv"
    frame.to_csv(path, index=False)
    return path


def fetch_renewables_ninja(
    lat: float,
    lon: float,
    year: int,
    token: str,
    dataset: str = "merra2",
    capacity: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Fetch hourly PV and wind capacity factors from Renewables.ninja.

    Returns ``(solar_cf, wind_cf)``, each an 8760-length array in [0, 1]. Requires a
    free API token. Trims/pads leap years to 8760 hours.
    """
    headers = {"Authorization": f"Token {token}"}
    common = {
        "lat": lat,
        "lon": lon,
        "date_from": f"{year}-01-01",
        "date_to": f"{year}-12-31",
        "dataset": dataset,
        "capacity": capacity,
        "format": "json",
        "local_time": "true",
    }

    def _series(kind: str, extra: dict) -> np.ndarray:
        query = urllib.parse.urlencode({**common, **extra})
        request = urllib.request.Request(f"{RENEWABLES_NINJA_BASE}/data/{kind}?{query}", headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode())
        values = [row["electricity"] for row in payload["data"].values()]
        series = np.asarray(values, dtype=float)[:HOURS_PER_YEAR]
        if len(series) < HOURS_PER_YEAR:
            series = np.pad(series, (0, HOURS_PER_YEAR - len(series)), mode="edge")
        return np.clip(series / max(capacity, 1e-9), 0.0, 1.0)

    solar_cf = _series("pv", {"system_loss": 0.1, "tracking": 0, "tilt": 35, "azim": 180})
    time.sleep(1)  # Renewables.ninja rate limit is ~6 requests/min.
    wind_cf = _series("wind", {"height": 100, "turbine": "Vestas V90 2000"})
    return solar_cf, wind_cf


def load_demand_csv(path: str | Path) -> np.ndarray:
    """Load a one-column hourly demand series (MW or any positive unit) → 8760 array."""
    frame = pd.read_csv(path)
    series = frame.iloc[:, -1].to_numpy(dtype=float)[:HOURS_PER_YEAR]
    if len(series) < HOURS_PER_YEAR:
        series = np.pad(series, (0, HOURS_PER_YEAR - len(series)), mode="edge")
    return np.clip(series, 1e-6, None)


def build_country_year(
    country: str,
    year: int,
    lat: float,
    lon: float,
    ninja_token: str,
    demand_csv: str | Path,
) -> Path:
    """End-to-end: fetch CF from Renewables.ninja, read demand CSV, write the profile."""
    solar_cf, wind_cf = fetch_renewables_ninja(lat, lon, year, ninja_token)
    demand = load_demand_csv(demand_csv)
    return write_profile_csv(country, year, demand, solar_cf, wind_cf)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a real hourly country profile.")
    parser.add_argument("--country", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--ninja-token", required=True)
    parser.add_argument("--demand-csv", required=True, help="One-column hourly demand series.")
    args = parser.parse_args()
    path = build_country_year(
        args.country, args.year, args.lat, args.lon, args.ninja_token, args.demand_csv
    )
    print(f"Wrote {path}")


if __name__ == "__main__":
    _main()
