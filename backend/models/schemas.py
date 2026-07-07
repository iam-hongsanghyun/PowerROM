from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AllowedFunctionType = Literal[
    "linear",
    "logarithmic",
    "quadratic",
    "exponential",
    "power",
    "piecewise",
    "constant",
    "multilinear",
]


class FunctionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AllowedFunctionType
    params: dict[str, float]
    x_min: float | None = None
    x_max: float | None = None
    source: str | None = None
    r_squared: float | None = None
    x_variable: str | None = None


class GeneratorOverride(BaseModel):
    model_config = ConfigDict(extra="allow")


class CalculateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str = Field(min_length=2, max_length=2)
    shares: dict[str, float] = Field(default_factory=dict)
    capacities_gw: dict[str, float] | None = None
    generator_order: list[str] | None = None
    carbon_price: float = Field(ge=0, le=500)
    ev_penetration: float = Field(default=0.0, ge=0.0, le=0.5)
    annual_demand_twh: float | None = Field(default=None, gt=0)
    custom_params: dict[str, Any] | None = None
    dispatch_mode: Literal["parametric", "data"] = "parametric"
    weather_years: list[int] | None = None
    ensemble: "EnsembleConfig | None" = None
    # User-set storage: power (GW) + duration (h) per tier; energy = power × duration.
    # None falls back to no storage (power 0) / the profile's duration.
    ess_short_power_gw: float | None = Field(default=None, ge=0)
    ess_short_duration_hr: float | None = Field(default=None, ge=0)
    ess_long_power_gw: float | None = Field(default=None, ge=0)
    ess_long_duration_hr: float | None = Field(default=None, ge=0)
    # Demand-shape controls for the synthesized load profile.
    demand_pattern: Literal["default", "winter_peak", "summer_peak", "flat"] = "default"
    demand_peak_ratio: float | None = Field(default=None, gt=1.0, le=4.0)
    # Visual demand editor: 12 monthly + 24 hourly relative levels (override the archetype).
    demand_monthly: list[float] | None = Field(default=None, min_length=12, max_length=12)
    demand_daily: list[float] | None = Field(default=None, min_length=24, max_length=24)
    # Capacity expansion: grow these generators to meet 100% load, cheapest-first.
    expandable: list[str] | None = None
    meet_full_load: bool = False
    # Renewable-target (RPS) policy lever.
    rps_target_share: float | None = Field(default=None, ge=0, le=1)
    rps_penalty_usd_mwh: float | None = Field(default=None, ge=0)
    # Clean-energy subsidy (applies to solar + wind + nuclear).
    subsidy_itc_pct: float | None = Field(default=None, ge=0, le=1)
    subsidy_ptc_usd_mwh: float | None = Field(default=None, ge=0)
    # Energy-security lever: fractional surcharge on imported fuel cost (gas/coal/other).
    fuel_import_tariff_pct: float | None = Field(default=None, ge=0, le=3)

    @model_validator(mode="after")
    def validate_shares(self) -> "CalculateRequest":
        if self.capacities_gw is not None:
            if not self.capacities_gw:
                raise ValueError("At least one generator capacity is required.")
            if any(value < 0 for value in self.capacities_gw.values()):
                raise ValueError("Capacities cannot be negative.")
            if sum(self.capacities_gw.values()) <= 0:
                raise ValueError("Capacities must sum to a positive value.")
            return self
        if not self.shares:
            raise ValueError("At least one generator share or capacity is required.")
        total = sum(self.shares.values())
        if total <= 0:
            raise ValueError("Shares must sum to a positive value.")
        if any(value < 0 for value in self.shares.values()):
            raise ValueError("Shares cannot be negative.")
        return self


class CurveDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vre_share: float
    system_lcoe: float
    emission_intensity: float
    ess_gwh: float
    ess_gw: float
    capex: float
    fuel: float
    carbon: float
    integration: float
    ess: float
    ess_short_gwh: float = 0.0
    ess_long_gwh: float = 0.0
    curtailment_rate: float = 0.0
    curtailed_twh: float = 0.0
    backup_flexibility: float = 1.0
    unserved_twh: float = 0.0


class EnsembleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["single", "jitter", "multiyear"] = "jitter"
    n_samples: int = Field(default=5, ge=1, le=50)
    sigma: float = Field(default=0.04, ge=0.0, le=0.5)
    seed: int = 42


class MetricBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p10: float
    median: float
    p90: float


class LdcSeriesBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p10: list[float]
    median: list[float]
    p90: list[float]


class LdcPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_hours: list[float]
    x_percent: list[float]
    series: dict[str, LdcSeriesBand]
    resource_order: list[str]


class ChronologicalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hours: list[int]
    series: dict[str, list[float]]
    resource_order: list[str]


class DataQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    share_normalized: bool | None = None
    used_custom_params: bool | None = None
    custom_override_fields: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CalculateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str
    shares: dict[str, float]
    capacity_shares: dict[str, float] = Field(default_factory=dict)
    capacities_gw: dict[str, float] = Field(default_factory=dict)
    annual_demand_twh: float
    system_lcoe: float
    system_lcoe_p10: float | None = None
    system_lcoe_p90: float | None = None
    annual_system_cost_usd_billion: float
    lcoe_by_generator: dict[str, dict[str, float | str]]
    emission_intensity: float
    emission_intensity_p10: float | None = None
    emission_intensity_p90: float | None = None
    annual_emissions_mtco2: float
    ess_requirement_gw: float
    ess_requirement_gwh: float
    ess_short_gwh: float = 0.0
    ess_short_gw: float = 0.0
    ess_short_lcoe: float = 0.0
    ess_long_gwh: float = 0.0
    ess_long_gw: float = 0.0
    ess_long_lcoe: float = 0.0
    curtailment_rate: float = 0.0
    curtailed_twh: float = 0.0
    unserved_twh: float = 0.0
    backup_flexibility: float = 1.0
    import_dependency: float = 0.0
    curve_data: list[CurveDataPoint]
    stack_components: dict[str, float]
    dispatch: dict[str, Any] | None = None
    ldc: LdcPayload | None = None
    chronological: ChronologicalPayload | None = None
    expansion: dict[str, Any] | None = None
    rps: dict[str, Any] | None = None
    data_quality: DataQuality


class PathwayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str = Field(min_length=2, max_length=2)
    start_capacities_gw: dict[str, float]
    target_capacities_gw: dict[str, float]
    years: list[int] = Field(min_length=1, max_length=12)
    carbon_price_start: float = Field(default=0.0, ge=0, le=500)
    carbon_price_end: float = Field(default=0.0, ge=0, le=500)
    annual_demand_twh_start: float | None = Field(default=None, gt=0)
    annual_demand_twh_end: float | None = Field(default=None, gt=0)
    ensemble: "EnsembleConfig | None" = None
    ess_short_power_gw: float | None = Field(default=None, ge=0)
    ess_short_duration_hr: float | None = Field(default=None, ge=0)
    ess_long_power_gw: float | None = Field(default=None, ge=0)
    ess_long_duration_hr: float | None = Field(default=None, ge=0)


class PathwayResponse(BaseModel):
    country: str
    years: list[int]
    steps: list[dict[str, Any]]


class DispatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str
    shares: dict[str, float]
    capacity_shares: dict[str, float] = Field(default_factory=dict)
    capacities_gw: dict[str, float] = Field(default_factory=dict)
    annual_demand_twh: float
    dispatch: dict[str, Any]
    ldc: LdcPayload
    chronological: ChronologicalPayload | None = None
    data_quality: DataQuality


class FitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_points: list[tuple[float, float]]
    func_type: AllowedFunctionType
    bounds: dict[str, list[float]] | None = None


class FitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: dict[str, float]
    r_squared: float
    confidence_intervals: dict[str, tuple[float, float]]
    sufficient_data: bool
    error_message: str | None = None
    data_quality: DataQuality


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generator_config: dict[str, Any]


class ValidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    components: dict[str, dict[str, Any]]
    data_quality: DataQuality


class CountrySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    annual_generation_twh: float
    discount_rate: float
    generators: list[str]
    sources: list[str]


class CountriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countries: list[CountrySummary]
    data_quality: DataQuality
