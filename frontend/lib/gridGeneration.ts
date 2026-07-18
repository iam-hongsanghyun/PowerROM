import { calculateBatch, type CalculateResponse, type GeneratorKey, type Shares } from "./api";

export interface ScatterPoint {
  x: number;
  y: number;
  z: number | null; // null for 2-generator grids
  xGenerator: GeneratorKey;
  yGenerator: GeneratorKey;
  zGenerator: GeneratorKey | null;
  shares: Shares;
  lcoe: number;
  emissions: number;
  annualCost: number;
  essGwh: number;
  response: CalculateResponse;
}

export interface GridConfig {
  country: string;
  carbonPrice: number;
  evPenetration: number;
  annualDemandTwh: number;
  axisGenerators: [GeneratorKey, GeneratorKey] | [GeneratorKey, GeneratorKey, GeneratorKey];
  /** All non-axis generators are fixed at these values from the left panel */
  fixedShares: Shares;
  resolution: number;
  /** Optional overrides passed to each calculate call (e.g. ESS cost) */
  customParams?: Record<string, unknown>;
  /** If provided, constrains the grid to within [min, max] for each axis generator */
  bounds?: {
    x: [number, number];
    y: [number, number];
    z?: [number, number];
  };
}

const EMPTY_SHARES: Shares = {
  solar: 0,
  wind_onshore: 0,
  wind_offshore: 0,
  gas_ccgt: 0,
  coal: 0,
  nuclear: 0,
  hydro: 0,
  other: 0,
};

/** Build a uniform linear range of `n` points between `min` and `max` (inclusive). */
function linspace(min: number, max: number, n: number): number[] {
  if (n === 1) return [(min + max) / 2];
  return Array.from({ length: n }, (_, i) => min + (i / (n - 1)) * (max - min));
}

/**
 * Generate a grid of generator mixes and compute LCOE/emissions for each.
 * Axis generators vary within their allowed ranges (constrained by fixed share total).
 * Non-axis generators are fixed at their left-panel values.
 */
export async function generateGrid(
  config: GridConfig,
  onProgress?: (completed: number, total: number) => void,
): Promise<ScatterPoint[]> {
  const { axisGenerators, fixedShares, resolution, bounds } = config;
  const is3D = axisGenerators.length === 3;

  const [xGen, yGen, zGen] = axisGenerators as [GeneratorKey, GeneratorKey, GeneratorKey | undefined];

  // Sum of fixed (non-axis) generators from the left panel
  const fixedTotal = Object.entries(fixedShares)
    .filter(([g]) => !(axisGenerators as GeneratorKey[]).includes(g as GeneratorKey))
    .reduce((s, [, v]) => s + v, 0);

  const maxCombined = Math.max(0, 1.0 - fixedTotal);

  const xRange = bounds?.x ?? [0, maxCombined];
  const yRange = bounds?.y ?? [0, maxCombined];
  const zRange = is3D ? (bounds?.z ?? [0, maxCombined]) : null;

  const xs = linspace(xRange[0], xRange[1], resolution);
  const ys = linspace(yRange[0], yRange[1], resolution);
  const zs = zRange ? linspace(zRange[0], zRange[1], resolution) : [0];

  // Build all valid combinations
  const combos: Array<{ x: number; y: number; z: number | null; shares: Shares }> = [];

  for (const x of xs) {
    for (const y of ys) {
      if (!is3D) {
        if (x + y > maxCombined + 1e-9) continue;
        const rawShares: Shares = {
          ...EMPTY_SHARES,
          ...fixedShares,
          [xGen]: x,
          [yGen]: y,
        };
        combos.push({ x, y, z: null, shares: rawShares });
      } else {
        for (const z of zs) {
          if (x + y + z > maxCombined + 1e-9) continue;
          const rawShares: Shares = {
            ...EMPTY_SHARES,
            ...fixedShares,
            [xGen]: x,
            [yGen]: y,
            [zGen!]: z,
          };
          combos.push({ x, y, z, shares: rawShares });
        }
      }
    }
  }

  const total = combos.length;
  if (total === 0) return [];

  const CHUNK = 50;
  const allResponses: CalculateResponse[] = [];

  for (let i = 0; i < total; i += CHUNK) {
    const chunk = combos.slice(i, i + CHUNK);
    const payloads = chunk.map(({ shares }) => ({
      country: config.country,
      shares,
      carbon_price: config.carbonPrice,
      ev_penetration: config.evPenetration,
      annual_demand_twh: config.annualDemandTwh,
      custom_params: config.customParams ?? null,
    }));
    const responses = await calculateBatch(payloads);
    allResponses.push(...responses);
    onProgress?.(Math.min(i + CHUNK, total), total);
  }

  return combos.map(({ x, y, z, shares }, idx) => {
    const r = allResponses[idx];
    return {
      x,
      y,
      z,
      xGenerator: xGen,
      yGenerator: yGen,
      zGenerator: (zGen ?? null) as GeneratorKey | null,
      shares,
      lcoe: r.system_lcoe,
      emissions: r.emission_intensity,
      annualCost: r.annual_system_cost_usd_billion,
      essGwh: r.ess_requirement_gwh,
      response: r,
    };
  });
}

/**
 * Extract bounding box from a selection of ScatterPoints for region refinement.
 */
export function refineBounds(
  selected: ScatterPoint[],
  padding = 0.04,
): { x: [number, number]; y: [number, number]; z?: [number, number] } {
  const xs = selected.map((p) => p.x);
  const ys = selected.map((p) => p.y);
  const clamp = (v: number): number => Math.max(0, Math.min(1, v));
  const result: { x: [number, number]; y: [number, number]; z?: [number, number] } = {
    x: [clamp(Math.min(...xs) - padding), clamp(Math.max(...xs) + padding)],
    y: [clamp(Math.min(...ys) - padding), clamp(Math.max(...ys) + padding)],
  };
  const zVals = selected.map((p) => p.z).filter((z): z is number => z !== null);
  if (zVals.length > 0) {
    result.z = [clamp(Math.min(...zVals) - padding), clamp(Math.max(...zVals) + padding)];
  }
  return result;
}
