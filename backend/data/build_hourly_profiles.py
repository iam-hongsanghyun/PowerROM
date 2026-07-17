"""Build real hourly weather-year profiles for the dispatch model's ``data`` mode.

Writes ``backend/data/hourly/<CC>/<year>.csv.gz`` with three 8760-hour columns —
``demand_norm`` (load shape, mean 1), ``solar_cf`` and ``wind_cf`` (capacity factors in
[0, 1]) — which ``backend/core/hourly_profiles.py`` loads directly.

All three columns derive from one **PVGIS/ERA5** request per country-year (EU JRC reanalysis,
no token) at the country's load-centre coordinates, whose hourly response carries PV power,
2 m air temperature (``T2m``) and 10 m wind speed (``WS10m``):

* **Solar** — PVGIS's own hourly PV capacity factor for a 1 kWp system.
* **Wind** — ``WS10m`` extrapolated to hub height (power law) through a fleet power curve,
  lightly time-smoothed to proxy the spatial diversity of a national fleet vs a single point.
* **Demand** — degree-hour thermal response to the country's real hourly temperature (heating
  below 16 °C, cooling above 22 °C) on top of the synthesizer's diurnal/weekend structure, so
  Gulf grids peak in summer afternoons and European grids in winter evenings because their
  actual weather says so.

Every series is mean-scaled so its annual average equals the profile's Ember-derived
``cf_base`` (solar/wind) or 1.0 (demand), so switching a country from synthetic to real
weather changes the *shape* of a year, never its annual energy.

Run:
    python -m backend.data.build_hourly_profiles                 # all countries
    python -m backend.data.build_hourly_profiles --countries KR DE US
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import io
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from backend.core.hourly_profiles import HOURS_PER_YEAR, _ar1_noise, synthesize_parametric
from backend.core.lcoe_engine import load_country_profile

HOURLY_DIR = Path(__file__).resolve().parent / "hourly"
# Three non-leap weather years (8760 h each) within PVGIS-ERA5's 2005–2020 range, so the ensemble
# sampler has real inter-annual variability to bootstrap (matching the synthetic path's multi-year).
WEATHER_YEARS = (2017, 2018, 2019)
PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc"
PVGIS_THROTTLE_S = 1.2  # spacing between PVGIS calls — bursts of ~100 requests get rate-limited (400)
# Raw PVGIS responses, keyed by (lat, lon, year) — git-ignored. Lets the wind/demand derivation
# be reworked and re-run offline instead of re-fetching ~500 responses each time.
PVGIS_CACHE_DIR = Path(__file__).resolve().parent / "pvgis_cache"

# ── Wind: ERA5 10 m speed → fleet capacity factor ────────────────────────────────
# v_hub = WS10m · (h_hub/10)^α (neutral-shear power law), then a fleet power curve:
# cf = ((v−v_ci)/(v_r−v_ci))³ clipped to [0,1] below rated, 1 to cut-out, 0 beyond. The cubic
# ramp and the parameters are standard fleet-aggregate stylizations (e.g. NREL/DTU power-curve
# literature); a light moving average proxies the spatial smoothing of a national fleet relative
# to a single reanalysis point. The result is mean-scaled to the country's Ember annual CF, so
# only the *shape* (diurnal/synoptic/seasonal timing) comes from the weather.
WIND_HUB_HEIGHT_M = 100.0
WIND_SHEAR_ALPHA = 0.14          # neutral-stability power-law exponent over open terrain
WIND_V_CUTIN_MS = 3.0
WIND_V_RATED_MS = 12.0
WIND_V_CUTOUT_MS = 25.0
WIND_FLEET_SMOOTH_H = 3          # centred moving average (h) ≈ national-fleet spatial diversity
# Wind farms are sited at the windiest locations, not at load centres, so the load-centre
# reanalysis point systematically under-reads the national fleet. Calibrate in the SPEED domain
# (the renewables.ninja bias-correction approach): v' = β·v with β solved by bisection so the
# power-curve output matches the Ember annual CF — moving to a windier site with the same
# synoptic timing. Post-hoc CF scaling cannot do this: it adds no energy to below-cut-in hours.
WIND_SPEED_CAL_MIN = 0.3
WIND_SPEED_CAL_MAX = 8.0  # KE needs ~6: Ember's 0.55 CF is Lake Turkana; Nairobi's point is calm

# ── Demand: real temperature → thermal load ─────────────────────────────────────
# Load responds linearly to degree-hours outside a comfort band: heating below 16 °C, cooling
# above 22 °C — the classic degree-day model with sensitivities in the range reported for
# temperature-sensitive power demand (~1–2 % of average load per °C). Diurnal (evening peak,
# business hours) and weekend terms mirror the parametric synthesizer so switching a country
# between modes changes the weather realism, not the anatomy of the shape.
DEMAND_HEAT_REF_C = 16.0
DEMAND_COOL_REF_C = 22.0
DEMAND_HEAT_SENS_PER_C = 0.012   # per-unit load per heating degree (°C below 16)
DEMAND_COOL_SENS_PER_C = 0.018   # per-unit load per cooling degree (°C above 22) — AC is steeper
DEMAND_NOISE_SIGMA = 0.02        # small AR(1) residual for non-thermal load variation
DEMAND_NOISE_RHO = 0.78

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


def fetch_pvgis_series(lat: float, lon: float, year: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hourly ``(pv_cf, t2m_c, ws10m)`` at ``(lat, lon)`` from one PVGIS/ERA5 request.

    ``pv_cf`` is the capacity factor of a 1 kWp system (0–1), ``t2m_c`` the 2 m air temperature
    (°C) and ``ws10m`` the 10 m wind speed (m/s). Each has exactly ``HOURS_PER_YEAR`` values
    (leap years are trimmed to 8760). All in UTC — callers roll to local time.
    """
    cache = PVGIS_CACHE_DIR / f"{lat:.2f}_{lon:.2f}_{year}.csv.gz"
    if cache.exists():
        text = gzip.decompress(cache.read_bytes()).decode("utf-8")
    else:
        query = urllib.parse.urlencode({
            "lat": lat, "lon": lon, "startyear": year, "endyear": year,
            "pvcalculation": 1, "peakpower": 1, "loss": 14, "outputformat": "csv",
            "raddatabase": "PVGIS-ERA5",  # global coverage (SARAH is EU/Africa/Asia only)
        })
        text = ""
        for attempt in range(4):
            time.sleep(PVGIS_THROTTLE_S * (attempt + 1))  # throttle + backoff on retry
            try:
                with urllib.request.urlopen(f"{PVGIS_URL}?{query}", timeout=90) as response:  # noqa: S310
                    text = response.read().decode("utf-8", errors="replace")
                break
            except Exception:  # noqa: BLE001 — transient PVGIS hiccup / rate limit; retry
                if attempt == 3:
                    raise
        PVGIS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(gzip.compress(text.encode("utf-8")))

    pv: list[float] = []
    t2m: list[float] = []
    ws: list[float] = []
    for line in text.splitlines():
        # Data rows: "20190101:0010,83.64,<G(i)>,<H_sun>,<T2m>,<WS10m>,<Int>" (header: time,P,...).
        if len(line) > 9 and line[:8].isdigit() and line[8] == ":":
            parts = line.split(",")
            if len(parts) >= 6:
                try:
                    pv.append(float(parts[1]) / 1000.0)  # W per 1 kWp → capacity factor
                    t2m.append(float(parts[4]))
                    ws.append(float(parts[5]))
                except ValueError:
                    continue
    if len(pv) < HOURS_PER_YEAR:
        raise ValueError(f"PVGIS returned only {len(pv)} hours for ({lat},{lon},{year})")
    trim = slice(0, HOURS_PER_YEAR)
    return (np.asarray(pv[trim]), np.asarray(t2m[trim]), np.asarray(ws[trim]))


def _fleet_power_curve(v_hub: np.ndarray) -> np.ndarray:
    """Fleet-aggregate power curve: cubic ramp between cut-in and rated, zero beyond cut-out."""
    ramp = np.clip((v_hub - WIND_V_CUTIN_MS) / (WIND_V_RATED_MS - WIND_V_CUTIN_MS), 0.0, 1.0)
    return np.where(v_hub >= WIND_V_CUTOUT_MS, 0.0, ramp**3)


def wind_cf_from_speed(ws10m: np.ndarray, wind_cf_base: float) -> np.ndarray:
    """Hourly wind capacity factor from ERA5 10 m wind speed, calibrated to the Ember annual CF.

    Algorithm:
        $$v = \\beta \\, v_{10}\\,(h_{hub}/10)^{\\alpha}, \\qquad
          cf_{raw} = \\mathrm{clip}\\!\\left(\\frac{v - v_{ci}}{v_r - v_{ci}}, 0, 1\\right)^{3}
          \\cdot \\mathbb{1}[v < v_{co}]$$
    ASCII: v = beta * ws10m * (100/10)^0.14; cf = clip((v-3)/(12-3),0,1)^3, 0 above 25 m/s;
    then a 3 h moving average (fleet spatial diversity) and a small residual mean-scale.

    Symbols: v_10 = 10 m speed (m/s); h_hub = 100 m hub height; α = 0.14 shear exponent;
    v_ci/v_r/v_co = 3/12/25 m/s cut-in/rated/cut-out; β = speed-calibration factor (bisection
    in [0.3, 5.0]) so the annual mean CF equals ``wind_cf_base`` — bias-correcting the
    load-centre point to the (windier) fleet sites while keeping real synoptic timing.

    Raises ``ValueError`` when the point is so calm that even β = 5 cannot reach the target
    (deep valley calm) — callers fall back to the calibrated synthesizer.
    """
    v_base = ws10m * (WIND_HUB_HEIGHT_M / 10.0) ** WIND_SHEAR_ALPHA

    def mean_cf(beta: float) -> float:
        return float(np.mean(_fleet_power_curve(beta * v_base)))

    # mean_cf(β) is not globally monotone (large β pushes storm hours past cut-out), so scan
    # upward for the FIRST bracket that crosses the target — the smallest, most physical
    # correction — then bisect inside it.
    lo = hi = None
    grid = np.linspace(WIND_SPEED_CAL_MIN, WIND_SPEED_CAL_MAX, 48)
    for prev, beta in zip(grid[:-1], grid[1:]):
        if mean_cf(prev) < wind_cf_base <= mean_cf(beta):
            lo, hi = float(prev), float(beta)
            break
    if lo is None:
        if mean_cf(float(grid[0])) >= wind_cf_base:
            lo, hi = WIND_SPEED_CAL_MIN, WIND_SPEED_CAL_MIN  # already at/above target
        else:
            raise ValueError("wind speed series has no usable energy content")
    for _ in range(60):  # bisection within the bracket (endpoints straddle the target)
        mid = 0.5 * (lo + hi)
        if mean_cf(mid) < wind_cf_base:
            lo = mid
        else:
            hi = mid
    kernel = np.ones(WIND_FLEET_SMOOTH_H) / WIND_FLEET_SMOOTH_H
    cf_smooth = np.convolve(_fleet_power_curve(hi * v_base), kernel, mode="same")
    return _mean_scale(cf_smooth, wind_cf_base, upper=0.98)  # residual exactness after smoothing


def demand_norm_from_temperature(t2m_c: np.ndarray, year: int, seed: int) -> np.ndarray:
    """Hourly demand shape (mean 1) from real temperature via the degree-hour model.

    Algorithm:
        $$d_h = 1 + k_H \\max(0, T_{ref,H} - T_h) + k_C \\max(0, T_h - T_{ref,C})
                + e_h + b_h - w_h + \\varepsilon_h$$
    ASCII: d = 1 + 0.012*max(0,16-T) + 0.018*max(0,T-22) + evening + business - weekend + AR1;
    then normalised to mean 1.

    Symbols: T_h = 2 m temperature (°C); k_H/k_C = heating/cooling sensitivity (per-unit load
    per °C); e_h/b_h = the synthesizer's evening-peak/business-hours Gaussians; w_h = 0.055
    weekend dip (real calendar weekday of ``year``); ε_h = AR(1) residual, σ = 0.02, ρ = 0.78.
    """
    hour_of_day = np.arange(HOURS_PER_YEAR) % 24
    day_of_year = np.arange(HOURS_PER_YEAR) // 24

    heating = DEMAND_HEAT_SENS_PER_C * np.maximum(0.0, DEMAND_HEAT_REF_C - t2m_c)
    cooling = DEMAND_COOL_SENS_PER_C * np.maximum(0.0, t2m_c - DEMAND_COOL_REF_C)
    evening_peak = 0.08 * np.exp(-(((hour_of_day - 19) / 4.2) ** 2))
    business_hours = 0.05 * np.exp(-(((hour_of_day - 13) / 5.0) ** 2))
    first_weekday = datetime.date(year, 1, 1).weekday()  # real calendar: 0 = Monday
    weekend = (((day_of_year + first_weekday) % 7) >= 5).astype(float)
    noise = _ar1_noise(np.random.default_rng(seed), HOURS_PER_YEAR,
                       sigma=DEMAND_NOISE_SIGMA, rho=DEMAND_NOISE_RHO)

    demand = 1.0 + heating + cooling + evening_peak + business_hours - 0.055 * weekend + noise
    demand = np.clip(demand, 0.05, None)
    return demand / float(np.mean(demand))


def _write_year(code: str, year: int, profile: dict, lat: float, lon: float) -> dict[str, float]:
    solar_cf_base = float(profile["generators"]["solar"]["cf_base"])
    wind_cf_base = float(profile["generators"]["wind_onshore"]["cf_base"])

    # One PVGIS/ERA5 request per year carries PV power, temperature and wind speed (UTC);
    # roll each to local time so midday/evening land on the model's local-hour indexing.
    pv, t2m, ws10m = (np.roll(series, UTC_OFFSET[code])
                      for series in fetch_pvgis_series(lat, lon, year))
    solar_cf = _mean_scale(pv, solar_cf_base, upper=0.96)
    try:
        wind_cf = wind_cf_from_speed(ws10m, wind_cf_base)
    except ValueError:
        # High-altitude valley load centres (BT, EC, NP, TJ) sit in near-permanent ERA5 calm —
        # a single point can't carry a national wind shape there, so fall back to the
        # calibrated synthetic wind (these countries have ~0 GW of wind fleet anyway).
        wind_cf = synthesize_parametric(code, load_country_profile(code),
                                        seed=42 + year, year=year).wind_cf
    # A per-year seed gives each weather year a distinct non-thermal load residual.
    demand_norm = demand_norm_from_temperature(t2m, year, seed=42 + year)

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
                  f"{s['wind_mean']:>10.4f}{s['wind_base']:>10.4f}  ERA5 solar/wind/temp-demand")
        except Exception as exc:  # noqa: BLE001 — report and continue the batch
            print(f"{code:<5}  ERROR: {exc}", file=sys.stderr)
    print(f"\nBuilt {ok}/{len(codes)} countries × {len(years)} years into {HOURLY_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
