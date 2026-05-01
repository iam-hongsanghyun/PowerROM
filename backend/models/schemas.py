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
]


class FunctionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AllowedFunctionType
    params: dict[str, float]
    x_min: float | None = None
    x_max: float | None = None
    source: str | None = None
    r_squared: float | None = None


class GeneratorOverride(BaseModel):
    model_config = ConfigDict(extra="allow")


class CalculateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str = Field(min_length=2, max_length=2)
    shares: dict[str, float]
    carbon_price: float = Field(ge=0, le=500)
    ev_penetration: float = Field(default=0.0, ge=0.0, le=0.5)
    annual_demand_twh: float | None = Field(default=None, gt=0)
    custom_params: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_shares(self) -> "CalculateRequest":
        if not self.shares:
            raise ValueError("At least one generator share is required.")
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
    annual_demand_twh: float
    system_lcoe: float
    annual_system_cost_usd_billion: float
    lcoe_by_generator: dict[str, dict[str, float | str]]
    emission_intensity: float
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
    backup_flexibility: float = 1.0
    curve_data: list[CurveDataPoint]
    stack_components: dict[str, float]
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
