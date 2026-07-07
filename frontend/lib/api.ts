export type GeneratorKey =
  | "solar"
  | "wind_onshore"
  | "gas_ccgt"
  | "coal"
  | "nuclear"
  | "other";

export type Shares = Record<GeneratorKey, number>;
export type Capacities = Record<GeneratorKey, number>;
export type DispatchMode = "parametric" | "data";
export type EnsembleMethod = "single" | "jitter" | "multiyear" | "block_bootstrap";

export interface EnsembleConfig {
  method: EnsembleMethod;
  n_samples: number;
  sigma: number;
  seed: number;
  block_days?: number;
}

export interface Adequacy {
  n_scenarios: number;
  lole_hours: number;
  lolp: number;
  loss_of_load_prob_annual: number;
  eue_mwh: number;
  eue_fraction: number;
  unserved_mwh_p50: number;
  unserved_mwh_p90: number;
  unserved_mwh_p95: number;
  unserved_mwh_p99: number;
  unserved_mwh_max: number;
  lole_hours_p50: number;
  lole_hours_p95: number;
  peak_shortfall_gw_p50: number;
  peak_shortfall_gw_p95: number;
  ensemble_method: EnsembleMethod;
}

export interface CurvePoint {
  vre_share: number;
  system_lcoe: number;
  emission_intensity: number;
  ess_gwh: number;
  ess_gw: number;
  capex: number;
  fuel: number;
  carbon: number;
  integration: number;
  ess: number;
  ess_short_gwh: number;
  ess_long_gwh: number;
  curtailment_rate: number;
  curtailed_twh: number;
  backup_flexibility: number;
  unserved_twh: number;
}

export interface CountrySummary {
  code: string;
  name: string;
  annual_generation_twh: number;
  discount_rate: number;
  generators: string[];
  sources: string[];
}

export interface DataQuality {
  share_normalized?: boolean;
  used_custom_params?: boolean;
  custom_override_fields: string[];
  sources: string[];
  notes: string[];
}

export interface CalculateResponse {
  country: string;
  shares: Record<string, number>;
  capacity_shares: Record<string, number>;
  capacities_gw: Record<string, number>;
  annual_demand_twh: number;
  system_lcoe: number;
  system_lcoe_p10?: number | null;
  system_lcoe_p90?: number | null;
  annual_system_cost_usd_billion: number;
  lcoe_by_generator: Record<string, Record<string, number | string>>;
  emission_intensity: number;
  emission_intensity_p10?: number | null;
  emission_intensity_p90?: number | null;
  annual_emissions_mtco2: number;
  ess_requirement_gw: number;
  ess_requirement_gwh: number;
  ess_short_gwh: number;
  ess_short_gw: number;
  ess_short_lcoe: number;
  ess_long_gwh: number;
  ess_long_gw: number;
  ess_long_lcoe: number;
  curtailment_rate: number;
  curtailed_twh: number;
  unserved_twh: number;
  backup_flexibility: number;
  import_dependency: number;
  curve_data: CurvePoint[];
  stack_components: Record<string, number>;
  dispatch?: DispatchSummary | null;
  ldc?: LdcPayload | null;
  chronological?: ChronologicalPayload | null;
  adequacy?: Adequacy | null;
  expansion?: {
    requested: string[];
    added_capacities_gw: Record<string, number>;
    note: string;
  } | null;
  rps?: {
    target_share: number;
    achieved_share: number;
    met: boolean;
    shortfall_share: number;
    penalty_lcoe: number;
  } | null;
  data_quality: DataQuality;
}

export interface MetricBand {
  p10: number;
  median: number;
  p90: number;
}

export interface LdcSeriesBand {
  p10: number[];
  median: number[];
  p90: number[];
}

export interface LdcPayload {
  x_hours: number[];
  x_percent: number[];
  series: Record<string, LdcSeriesBand>;
  resource_order: string[];
}

export interface ChronologicalPayload {
  hours: number[];
  series: Record<string, number[]>;
  resource_order: string[];
}

export interface DispatchSummary {
  mode: DispatchMode;
  ensemble: {
    method: EnsembleMethod;
    n_samples: number;
    sigma: number;
    seed: number;
    sources: string[];
    years: number[];
  };
  metrics: {
    scalars: Record<string, MetricBand>;
    capacity_factor: Record<string, MetricBand>;
    realized_share: Record<string, MetricBand>;
    energy_twh: Record<string, MetricBand>;
    capacity_gw: Record<string, MetricBand>;
    capacity_share: Record<string, MetricBand>;
    curtailment_rate_by_generator: Record<string, MetricBand>;
  };
}

export interface DispatchResponse {
  country: string;
  shares: Record<string, number>;
  capacity_shares: Record<string, number>;
  capacities_gw: Record<string, number>;
  annual_demand_twh: number;
  dispatch: DispatchSummary;
  ldc: LdcPayload;
  chronological?: ChronologicalPayload | null;
  data_quality: DataQuality;
}

export interface CountriesResponse {
  countries: CountrySummary[];
  data_quality: DataQuality;
}

export interface FitResponse {
  params: Record<string, number>;
  r_squared: number;
  confidence_intervals: Record<string, [number, number]>;
  sufficient_data: boolean;
  error_message?: string | null;
  data_quality: DataQuality;
}

export interface ValidateResponse {
  status: string;
  components: Record<string, Record<string, string | number | null>>;
  data_quality: DataQuality;
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `API request failed: ${response.status}`);
  }

  return (await response.json()) as T;
}

export async function fetchCountries(): Promise<CountriesResponse> {
  return request<CountriesResponse>("/countries");
}

export async function calculateSystem(payload: {
  country: string;
  shares?: Shares;
  capacities_gw?: Capacities;
  generator_order?: GeneratorKey[];
  carbon_price: number;
  ev_penetration?: number;
  annual_demand_twh?: number;
  custom_params?: Record<string, unknown> | null;
  dispatch_mode?: DispatchMode;
  weather_years?: number[] | null;
  ensemble?: EnsembleConfig | null;
  ess_short_power_gw?: number | null;
  ess_short_duration_hr?: number | null;
  ess_long_power_gw?: number | null;
  ess_long_duration_hr?: number | null;
  demand_pattern?: "default" | "winter_peak" | "summer_peak" | "flat";
  demand_peak_ratio?: number | null;
  demand_monthly?: number[] | null;
  demand_daily?: number[] | null;
  expandable?: string[];
  meet_full_load?: boolean;
  rps_target_share?: number | null;
  rps_penalty_usd_mwh?: number | null;
  subsidy_itc_pct?: number | null;
  subsidy_ptc_usd_mwh?: number | null;
  fuel_import_tariff_pct?: number | null;
}): Promise<CalculateResponse> {
  return request<CalculateResponse>("/calculate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function dispatchSystem(payload: {
  country: string;
  shares?: Shares;
  capacities_gw?: Capacities;
  generator_order?: GeneratorKey[];
  carbon_price: number;
  ev_penetration?: number;
  annual_demand_twh?: number;
  custom_params?: Record<string, unknown> | null;
  dispatch_mode?: DispatchMode;
  weather_years?: number[] | null;
  ensemble?: EnsembleConfig | null;
  ess_short_power_gw?: number | null;
  ess_short_duration_hr?: number | null;
  ess_long_power_gw?: number | null;
  ess_long_duration_hr?: number | null;
  demand_pattern?: "default" | "winter_peak" | "summer_peak" | "flat";
  demand_peak_ratio?: number | null;
  demand_monthly?: number[] | null;
  demand_daily?: number[] | null;
  expandable?: string[];
  meet_full_load?: boolean;
  rps_target_share?: number | null;
  rps_penalty_usd_mwh?: number | null;
  subsidy_itc_pct?: number | null;
  subsidy_ptc_usd_mwh?: number | null;
  fuel_import_tariff_pct?: number | null;
}): Promise<DispatchResponse> {
  return request<DispatchResponse>("/dispatch", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface PathwayStep {
  year: number;
  fraction: number;
  carbon_price: number;
  annual_demand_twh: number;
  system_lcoe: number;
  annual_emissions_mtco2: number;
  emission_intensity: number;
  import_dependency: number;
  capacities_gw: Record<string, number>;
}

export interface PathwayResponse {
  country: string;
  years: number[];
  steps: PathwayStep[];
}

export async function simulatePathway(payload: {
  country: string;
  start_capacities_gw: Capacities;
  target_capacities_gw: Capacities;
  years: number[];
  carbon_price_start: number;
  carbon_price_end: number;
  annual_demand_twh_start?: number | null;
  annual_demand_twh_end?: number | null;
  ensemble?: EnsembleConfig | null;
  ess_short_power_gw?: number | null;
  ess_short_duration_hr?: number | null;
  ess_long_power_gw?: number | null;
  ess_long_duration_hr?: number | null;
}): Promise<PathwayResponse> {
  return request<PathwayResponse>("/pathway", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function calculateBatch(
  payloads: Array<{
    country: string;
    shares: Shares;
    carbon_price: number;
    ev_penetration?: number;
    annual_demand_twh?: number;
  }>,
): Promise<CalculateResponse[]> {
  return request<CalculateResponse[]>("/calculate-batch", {
    method: "POST",
    body: JSON.stringify(payloads),
  });
}

export async function fitCurve(payload: {
  data_points: Array<[number, number]>;
  func_type: string;
  bounds?: { min: number[]; max: number[] };
}): Promise<FitResponse> {
  return request<FitResponse>("/fit", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function validateGeneratorConfig(payload: {
  generator_config: Record<string, unknown>;
}): Promise<ValidateResponse> {
  return request<ValidateResponse>("/validate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// Profile (country parameter) API
// ---------------------------------------------------------------------------

export interface FuncConfig {
  type: string;
  params: Record<string, number>;
  x_min?: number;
  x_max?: number;
  source?: string;
  x_variable?: string;
}

export interface GeneratorConfig {
  capex_usd_kw?: number;
  opex_fixed_usd_kw_yr?: number;
  opex_var_usd_mwh?: number;
  lifetime_yr?: number;
  emission_factor_tco2_mwh?: number;
  fuel_usd_mmbtu?: number;
  heat_rate_mmbtu_mwh?: number;
  cf_base?: number;
  // Capacity factor, curtailment, and integration cost are derived from the country's
  // hourly dispatch pattern — the only fitted curve is thermal part-load efficiency.
  eta_func?: FuncConfig;
  variability_factor?: number;
}

export interface EssShortDurConfig {
  capex_usd_kwh?: number;
  lifetime_yr?: number;
  cycles_per_year?: number;
  dod?: number;
  duration_hr?: number;
  ev_offset_gwh_per_unit?: number;
  solar_absorption_fraction?: number;
  wind_onshore_absorption_fraction?: number;
}

export interface EssLongDurConfig {
  capex_usd_kwh?: number;
  lifetime_yr?: number;
  cycles_per_year?: number;
  dod?: number;
  duration_hr?: number;
  threshold?: number;
  requirement_func?: FuncConfig;
}

export interface EssConfig {
  short_dur?: EssShortDurConfig;
  long_dur?: EssLongDurConfig;
  // legacy flat fields
  capex_usd_kwh?: number;
  lifetime_yr?: number;
  cycles_per_year?: number;
  dod?: number;
  ev_offset_gwh_per_unit?: number;
  requirement_func?: FuncConfig;
}

export interface CountryProfile {
  name: string;
  annual_generation_twh: number;
  discount_rate: number;
  generators: Record<string, GeneratorConfig>;
  ess: EssConfig;
  sources?: string[];
}

export async function fetchProfile(country: string): Promise<CountryProfile> {
  return request<CountryProfile>(`/profile/${country}`);
}

/** Parse an Excel file on the backend (no disk write) and return the profile. */
export async function parseExcelProfile(file: File): Promise<CountryProfile> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}/profile/excel/parse`, {
    method: "POST",
    body: formData,
    cache: "no-store",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Parse failed: ${response.status}`);
  }
  return (await response.json()) as CountryProfile;
}

export function profileExcelDownloadUrl(country: string): string {
  return `${API_BASE_URL}/profile/${country}/excel`;
}
