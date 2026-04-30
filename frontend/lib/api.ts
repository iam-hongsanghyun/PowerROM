export type GeneratorKey =
  | "solar"
  | "wind_onshore"
  | "gas_ccgt"
  | "coal"
  | "nuclear"
  | "other";

export type Shares = Record<GeneratorKey, number>;

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
  annual_demand_twh: number;
  system_lcoe: number;
  annual_system_cost_usd_billion: number;
  lcoe_by_generator: Record<string, Record<string, number | string>>;
  emission_intensity: number;
  annual_emissions_mtco2: number;
  ess_requirement_gw: number;
  ess_requirement_gwh: number;
  curve_data: CurvePoint[];
  stack_components: Record<string, number>;
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
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000/api";

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
  shares: Shares;
  carbon_price: number;
  ev_penetration?: number;
  annual_demand_twh?: number;
  custom_params?: Record<string, unknown> | null;
}): Promise<CalculateResponse> {
  return request<CalculateResponse>("/calculate", {
    method: "POST",
    body: JSON.stringify(payload),
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
