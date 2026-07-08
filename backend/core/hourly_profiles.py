from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.special import ndtr

HOURS_PER_YEAR = 8760

# Weibull shape for the synthetic wind capacity factor. k≈1.8 reproduces the ~0.6 coefficient of
# variation of real onshore wind — frequent near-calm hours and a fat high-output tail — instead of
# a value that hovers near its mean (which would make a large wind fleet behave like baseload).
_WIND_WEIBULL_K = 1.8
HOURLY_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "hourly"

ProfileMode = Literal["parametric", "data"]
EnsembleMethod = Literal["single", "jitter", "multiyear", "block_bootstrap"]


@dataclass(frozen=True)
class YearProfile:
    country: str
    year: int
    demand_norm: np.ndarray
    solar_cf: np.ndarray
    wind_cf: np.ndarray
    source: str


@dataclass(frozen=True)
class EnsembleSettings:
    method: EnsembleMethod = "single"
    n_samples: int = 1
    sigma: float = 0.04
    seed: int = 42
    # Block length (days) for the coherent block-bootstrap sampler; must exceed the synoptic
    # weather timescale (~3–7 days) so multi-day droughts are not chopped in half.
    block_days: int = 14


def normalize_hourly_profile(
    demand_norm: np.ndarray,
    solar_cf: np.ndarray,
    wind_cf: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    demand = np.asarray(demand_norm, dtype=float)
    solar = np.asarray(solar_cf, dtype=float)
    wind = np.asarray(wind_cf, dtype=float)

    if not (len(demand) == len(solar) == len(wind) == HOURS_PER_YEAR):
        raise ValueError("Hourly profiles must contain exactly 8760 rows.")
    if np.any(demand <= 0):
        raise ValueError("Demand profile values must be positive.")

    demand = demand / float(np.mean(demand))
    solar = np.clip(solar, 0.0, 1.0)
    wind = np.clip(wind, 0.0, 1.0)
    return demand, solar, wind


# Demand archetypes scale the seasonal/diurnal components of the synthesized load.
# "flat" damps them (high load factor); winter/summer emphasise one season.
DEMAND_ARCHETYPES: dict[str, dict[str, float]] = {
    "default": {"winter": 1.0, "summer": 1.0, "evening": 1.0, "business": 1.0},
    "winter_peak": {"winter": 1.8, "summer": 0.4, "evening": 1.2, "business": 0.9},
    "summer_peak": {"winter": 0.4, "summer": 1.8, "evening": 1.0, "business": 1.3},
    "flat": {"winter": 0.3, "summer": 0.3, "evening": 0.4, "business": 0.4},
}


def _apply_peak_ratio(shape: np.ndarray, peak_ratio: float) -> np.ndarray:
    """Rescale a load shape so its peak÷mean equals ``peak_ratio``, mean unchanged.

    Applies a gain to the deviations from the mean (mean-preserving), so annual energy
    is conserved after normalisation and the trough follows from the shape. A higher
    ratio = peakier demand (lower load factor).
    """
    mean = float(np.mean(shape))
    if mean <= 0:
        return shape
    current_peak_ratio = float(np.max(shape)) / mean
    if current_peak_ratio <= 1.0:
        return shape
    gain = (peak_ratio - 1.0) / (current_peak_ratio - 1.0)
    return mean + (shape - mean) * max(0.0, gain)


# Day-of-year index at which each calendar month begins (non-leap).
_MONTH_STARTS = np.array([0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334])


# ── VRE drought (Dunkelflaute) ──────────────────────────────────────────────────
# A smooth synthetic wind profile never truly calms (its min CF sits near its noise floor),
# which hands a large wind fleet unearned firm-capacity credit and makes "renewables firm the
# load on their own" look far too easy. Real systems have multi-day, winter-clustered near-calm
# AND overcast events (Dunkelflaute) coincident with peak demand — these set the reliability
# constraint. We inject a few per synthetic year so the binding hour is a realistic renewable
# drought that only firm generation or long-duration storage can cover.
_VRE_DROUGHT_EVENTS: int = 3
_VRE_DROUGHT_MIN_HR: int = 36
_VRE_DROUGHT_MAX_HR: int = 72
_VRE_DROUGHT_SHOULDER_HR: int = 12      # cosine ramp in/out; the core between is at full suppression
_VRE_DROUGHT_WIND_FLOOR: float = 0.05   # wind shape multiplier across the trough core (deep calm)
_VRE_DROUGHT_SOLAR_FLOOR: float = 0.15  # solar shape multiplier across the trough core (heavy overcast)
_VRE_DROUGHT_WINTER_DAY: int = 20       # deep-winter day-of-year the events cluster around (N. hemisphere)
_VRE_DROUGHT_SPREAD_DAY: int = 45       # ± spread of event centres, days
_VRE_DROUGHT_SEED_OFFSET: int = 8191    # separate RNG stream so base CF/demand noise is unchanged


def _vre_drought_masks(seed: int, southern: bool) -> tuple[np.ndarray, np.ndarray]:
    """Multiplicative wind/solar suppression masks for winter Dunkelflaute events.

    Each event is a multi-day flat-bottomed trough (cosine shoulders of ``_VRE_DROUGHT_SHOULDER_HR``
    ramping into a core held at full suppression) dipping the *shape* of wind to
    ``_VRE_DROUGHT_WIND_FLOOR`` and solar to ``_VRE_DROUGHT_SOLAR_FLOOR``, clustered around deep
    winter. Applied before mean-scaling, so annual energy (the profile's ``cf_base``) is
    preserved — the drought redistributes generation rather than deleting it.
    Drawn from a *separate* RNG stream keyed off ``seed`` so the base CF and demand noise, and
    therefore every existing result that does not fall in a drought window, is unchanged.

    Args:
        seed: Base profile seed; the drought stream is ``seed + _VRE_DROUGHT_SEED_OFFSET``.
        southern: Southern-hemisphere flag — shifts the winter cluster by half a year.

    Returns:
        ``(wind_mask, solar_mask)`` each an 8760-hour array in ``[floor, 1]``.
    """
    drng = np.random.default_rng(seed + _VRE_DROUGHT_SEED_OFFSET)
    wind_mask = np.ones(HOURS_PER_YEAR, dtype=float)
    solar_mask = np.ones(HOURS_PER_YEAR, dtype=float)
    winter_day = (_VRE_DROUGHT_WINTER_DAY + 182) % 365 if southern else _VRE_DROUGHT_WINTER_DAY
    for _ in range(_VRE_DROUGHT_EVENTS):
        centre_day = (winter_day + int(drng.integers(-_VRE_DROUGHT_SPREAD_DAY, _VRE_DROUGHT_SPREAD_DAY + 1))) % 365
        duration = int(drng.integers(_VRE_DROUGHT_MIN_HR, _VRE_DROUGHT_MAX_HR + 1))
        start = centre_day * 24 - duration // 2
        idx = np.arange(start, start + duration) % HOURS_PER_YEAR
        # Flat-bottomed trough: cosine shoulders ramp 0→1, a core held at 1 (full suppression).
        ramp = min(_VRE_DROUGHT_SHOULDER_HR, duration // 2)
        bump = np.ones(duration, dtype=float)
        shoulder = 0.5 * (1.0 - np.cos(np.pi * np.arange(1, ramp + 1) / ramp))  # 0 → 1
        bump[:ramp] = shoulder
        bump[duration - ramp:] = shoulder[::-1]
        wind_mask[idx] = np.minimum(wind_mask[idx], 1.0 - (1.0 - _VRE_DROUGHT_WIND_FLOOR) * bump)
        solar_mask[idx] = np.minimum(solar_mask[idx], 1.0 - (1.0 - _VRE_DROUGHT_SOLAR_FLOOR) * bump)
    return wind_mask, solar_mask


def synthesize_parametric(
    country: str,
    profile: dict[str, Any],
    seed: int = 42,
    year: int = 2024,
    demand_pattern: str = "default",
    demand_peak_ratio: float | None = None,
    demand_monthly: list[float] | None = None,
    demand_daily: list[float] | None = None,
) -> YearProfile:
    rng = np.random.default_rng(seed)
    hour = np.arange(HOURS_PER_YEAR)
    hour_of_day = hour % 24
    day_of_year = hour // 24
    country_code = country.upper()

    southern = country_code == "AU"
    season_sign = -1.0 if southern else 1.0
    winter_peak_phase = 10 if southern else 192
    summer_peak_phase = 15 if southern else 205

    solar_base = float(profile["generators"].get("solar", {}).get("cf_base", 0.18))
    wind_base = float(profile["generators"].get("wind_onshore", {}).get("cf_base", 0.28))

    # Winter Dunkelflaute troughs (multi-day near-calm + overcast) so the reliability-binding
    # hour is a realistic renewable drought, not the smooth profile's artificial wind floor.
    wind_drought_mask, solar_drought_mask = _vre_drought_masks(seed, southern)

    daylight = np.sin(np.pi * (hour_of_day - 6) / 12)
    daylight = np.clip(daylight, 0.0, None) ** 1.45
    # Solar peaks at the summer solstice (~day 172 N / flipped by season_sign for the S hemisphere)
    # and bottoms in winter — NOT the reverse. A winter peak here would overstate winter solar and
    # mask exactly the cold-season low-VRE adequacy risk this model is built to measure.
    solar_seasonal = 1.0 + season_sign * 0.24 * np.cos(2 * np.pi * (day_of_year - 172) / 365)
    solar_noise = np.clip(rng.normal(1.0, 0.10, HOURS_PER_YEAR), 0.55, 1.25)
    solar_shape = np.clip(daylight * solar_seasonal * solar_noise, 0.0, None) * solar_drought_mask
    solar_cf = _scale_to_mean(solar_shape, solar_base, upper=0.96)

    # Wind: a persistent Weibull-distributed capacity factor. A standard AR(1) Gaussian (sigma=1)
    # gives multi-hour persistence; mapping it through the normal CDF to a uniform and then a
    # Weibull(shape k) inverse-CDF yields a CF with the real ~0.6 CV — real calms and real windy
    # spells — rather than the old low-variance noise that sat near its mean. The winter-high
    # seasonal cycle, a weak diurnal term and the Dunkelflaute troughs modulate it; the result is
    # mean-scaled so annual energy still equals the profile's cf_base.
    wind_z = _ar1_noise(rng, HOURS_PER_YEAR, sigma=1.0, rho=0.93)
    wind_uniform = np.clip(ndtr(wind_z), 1e-6, 1.0 - 1e-6)
    wind_weibull = (-np.log(1.0 - wind_uniform)) ** (1.0 / _WIND_WEIBULL_K)
    wind_seasonal = 1.0 + season_sign * 0.16 * np.cos(2 * np.pi * (day_of_year - 25) / 365)
    wind_diurnal = 1.0 + 0.08 * np.sin(2 * np.pi * (hour_of_day - 2) / 24)
    wind_shape = wind_weibull * wind_seasonal * wind_diurnal * wind_drought_mask
    wind_cf = _scale_to_mean(wind_shape, wind_base, upper=1.0)

    if demand_monthly is not None and demand_daily is not None:
        # User-drawn shape: monthly seasonal level × daily (hour-of-day) pattern.
        monthly = np.asarray(demand_monthly, dtype=float)
        daily = np.asarray(demand_daily, dtype=float)
        month_index = np.clip(np.searchsorted(_MONTH_STARTS, day_of_year, side="right") - 1, 0, 11)
        demand_noise = _ar1_noise(rng, HOURS_PER_YEAR, sigma=0.02, rho=0.78)
        demand_shape = monthly[month_index] * daily[hour_of_day] * (1.0 + demand_noise)
    else:
        w = DEMAND_ARCHETYPES.get(demand_pattern, DEMAND_ARCHETYPES["default"])
        winter_component = w["winter"] * 0.10 * np.cos(2 * np.pi * (day_of_year - winter_peak_phase) / 365)
        summer_component = w["summer"] * 0.08 * np.cos(4 * np.pi * (day_of_year - summer_peak_phase) / 365)
        evening_peak = w["evening"] * 0.08 * np.exp(-((hour_of_day - 19) / 4.2) ** 2)
        business_hours = w["business"] * 0.05 * np.exp(-((hour_of_day - 13) / 5.0) ** 2)
        weekend = ((day_of_year + 1) % 7 >= 5).astype(float)
        demand_noise = _ar1_noise(rng, HOURS_PER_YEAR, sigma=0.025, rho=0.78)
        demand_shape = (
            1.0
            + winter_component
            + summer_component
            + evening_peak
            + business_hours
            - 0.055 * weekend
            + demand_noise
        )
        if demand_peak_ratio is not None and demand_peak_ratio > 1.0:
            demand_shape = _apply_peak_ratio(demand_shape, float(demand_peak_ratio))
    demand_norm = np.clip(demand_shape, 0.05, None)

    demand_norm, solar_cf, wind_cf = normalize_hourly_profile(demand_norm, solar_cf, wind_cf)
    return YearProfile(
        country=country_code,
        year=year,
        demand_norm=demand_norm,
        solar_cf=solar_cf,
        wind_cf=wind_cf,
        source="parametric_synthetic",
    )


def load_hourly_profiles(
    country: str,
    profile: dict[str, Any],
    mode: ProfileMode = "parametric",
    years: list[int] | None = None,
    seed: int = 42,
    demand_pattern: str = "default",
    demand_peak_ratio: float | None = None,
    demand_monthly: list[float] | None = None,
    demand_daily: list[float] | None = None,
) -> list[YearProfile]:
    country_code = country.upper()
    if mode == "data":
        loaded = _load_data_profiles(country_code, years)
        if loaded:
            return loaded

    selected_years = years or [2020, 2021, 2022, 2023, 2024]
    return [
        synthesize_parametric(
            country_code, profile, seed=seed + index * 101, year=year,
            demand_pattern=demand_pattern, demand_peak_ratio=demand_peak_ratio,
            demand_monthly=demand_monthly, demand_daily=demand_daily,
        )
        for index, year in enumerate(selected_years)
    ]


def sample_ensemble(
    base_profiles: list[YearProfile],
    settings: EnsembleSettings | None = None,
) -> list[YearProfile]:
    if not base_profiles:
        raise ValueError("At least one hourly profile is required.")

    cfg = settings or EnsembleSettings()
    n_samples = max(1, int(cfg.n_samples))
    rng = np.random.default_rng(cfg.seed)

    if cfg.method == "single":
        return [base_profiles[0]]

    if cfg.method == "multiyear":
        return [base_profiles[index % len(base_profiles)] for index in range(n_samples)]

    if cfg.method == "block_bootstrap":
        block_hours = max(24, int(cfg.block_days) * 24)
        return [_block_bootstrap_profile(base_profiles, rng, block_hours, index) for index in range(n_samples)]

    samples: list[YearProfile] = []
    for index in range(n_samples):
        base = base_profiles[index % len(base_profiles)]
        samples.append(_jitter_profile(base, rng, sigma=max(0.0, float(cfg.sigma)), index=index))
    return samples


def _load_data_profiles(country: str, years: list[int] | None) -> list[YearProfile]:
    data_dir = HOURLY_DATA_DIR / country
    if not data_dir.exists():
        return []

    def _year_paths(year: int) -> list[Path]:
        # Accept either plain or gzipped CSV (the builder writes .csv.gz to keep the repo small).
        return [p for p in (data_dir / f"{year}.csv", data_dir / f"{year}.csv.gz") if p.exists()]

    if years:
        paths = [p for year in years for p in _year_paths(year)]
    else:
        paths = sorted(data_dir.glob("*.csv")) + sorted(data_dir.glob("*.csv.gz"))

    profiles: list[YearProfile] = []
    for path in paths:
        if not path.exists():
            continue
        frame = pd.read_csv(path)  # pandas reads .gz transparently by extension
        required = {"demand_norm", "solar_cf", "wind_cf"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"{path} is missing hourly columns: {sorted(missing)}")

        demand_norm, solar_cf, wind_cf = normalize_hourly_profile(
            frame["demand_norm"].to_numpy(),
            frame["solar_cf"].to_numpy(),
            frame["wind_cf"].to_numpy(),
        )
        # Filename stem is like "2019" or "2019.csv" (compound suffix) — take the leading digits.
        stem = path.name.split(".")[0]
        year = int(stem) if stem.isdigit() else 0
        profiles.append(
            YearProfile(
                country=country,
                year=year,
                demand_norm=demand_norm,
                solar_cf=solar_cf,
                wind_cf=wind_cf,
                source=f"data:{path.name}",
            )
        )
    return profiles


def _block_bootstrap_profile(
    pool: list[YearProfile],
    rng: np.random.Generator,
    block_hours: int,
    index: int,
) -> YearProfile:
    """Assemble one synthetic year by **calendar-aligned** coherent block bootstrap.

    Each contiguous block of the synthetic year is copied from the *same calendar position* of a
    randomly chosen source year in ``pool``. This is the correlation-correct sampler for adequacy:

    * **Seasonality** is preserved — a block at week *k* is only ever drawn from week *k* of some
      year, so the winter demand peak never lands on a summer VRE block.
    * **Temporal + cross-variable dependence** inside a block is preserved for free, because the
      block is a real contiguous slice with demand, solar and wind moving together.
    * A **new year** is manufactured by varying which weather year supplies each block, so
      low-renewable blocks recombine into multi-day droughts at a realistic frequency — which
      independent hourly/annual sampling would factorise away, badly under-stating LOLE.

    The block length must exceed the synoptic weather timescale (~3–7 days) or droughts get chopped
    at block seams; ``block_hours`` defaults to two weeks. Seam discontinuities between blocks are
    left un-blended — for an energy-balance adequacy metric they are immaterial.
    """
    demand = np.empty(HOURS_PER_YEAR, dtype=float)
    solar = np.empty(HOURS_PER_YEAR, dtype=float)
    wind = np.empty(HOURS_PER_YEAR, dtype=float)
    n_years = len(pool)
    position = 0
    while position < HOURS_PER_YEAR:
        take = min(block_hours, HOURS_PER_YEAR - position)
        source = pool[int(rng.integers(n_years))]
        window = slice(position, position + take)
        demand[window] = source.demand_norm[window]
        solar[window] = source.solar_cf[window]
        wind[window] = source.wind_cf[window]
        position += take
    demand, solar, wind = normalize_hourly_profile(demand, solar, wind)
    return YearProfile(
        country=pool[0].country,
        year=pool[0].year,
        demand_norm=demand,
        solar_cf=solar,
        wind_cf=wind,
        source=f"block_bootstrap:{index}",
    )


def _jitter_profile(
    base: YearProfile,
    rng: np.random.Generator,
    sigma: float,
    index: int,
) -> YearProfile:
    if sigma <= 0:
        return base

    demand = np.clip(base.demand_norm * (1.0 + _ar1_noise(rng, HOURS_PER_YEAR, sigma, 0.90)), 0.35, None)
    solar = np.clip(base.solar_cf * (1.0 + _ar1_noise(rng, HOURS_PER_YEAR, sigma * 1.35, 0.86)), 0.0, 1.0)
    wind = np.clip(base.wind_cf * (1.0 + _ar1_noise(rng, HOURS_PER_YEAR, sigma * 1.55, 0.92)), 0.0, 1.0)
    demand, solar, wind = normalize_hourly_profile(demand, solar, wind)
    return YearProfile(
        country=base.country,
        year=base.year,
        demand_norm=demand,
        solar_cf=solar,
        wind_cf=wind,
        source=f"{base.source}:jitter:{index}",
    )


def _scale_to_mean(values: np.ndarray, target_mean: float, upper: float) -> np.ndarray:
    mean = float(np.mean(values))
    if mean <= 0:
        return np.zeros_like(values, dtype=float)
    scaled = values * (target_mean / mean)
    return np.clip(scaled, 0.0, upper)


def _ar1_noise(
    rng: np.random.Generator,
    size: int,
    sigma: float,
    rho: float,
) -> np.ndarray:
    innovations = rng.normal(0.0, sigma, size)
    values = np.empty(size, dtype=float)
    values[0] = innovations[0]
    scale = np.sqrt(max(0.0, 1.0 - rho**2))
    for index in range(1, size):
        values[index] = rho * values[index - 1] + scale * innovations[index]
    return values
