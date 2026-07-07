/**
 * Shared front-end constants for generator metadata and country defaults.
 *
 * Keep these in sync with:
 *   backend/core/lcoe_engine.py   — VRE_GENERATORS
 */

// ── Generator registry ────────────────────────────────────────────────────────

/** Keys that identify intermittent (variable renewable) generators. */
export const VRE_GENERATOR_KEYS = new Set(["solar", "wind_onshore"]);

/**
 * All generator keys in the merit-order panel's display order (top → bottom).
 * Reversed so the peaking/most-expensive end sits at the top. This is display only —
 * the actual dispatch order is computed from marginal cost in the backend
 * (backend/core/dispatch_engine.py), independent of this list.
 */
export const ALL_GENERATOR_KEYS = [
  "other",
  "gas_ccgt",
  "coal",
  "nuclear",
  "wind_onshore",
  "solar",
] as const;

/** Human-readable labels for each generator type. */
export const GENERATOR_LABELS: Record<string, string> = {
  solar: "Solar",
  wind_onshore: "Wind (Onshore)",
  gas_ccgt: "Gas CCGT",
  coal: "Coal",
  nuclear: "Nuclear",
  other: "Other",
};

/** Chart accent colours for each generator type. */
export const GENERATOR_COLORS: Record<string, string> = {
  solar: "#f59e0b",       // amber
  wind_onshore: "#3b82f6", // blue
  gas_ccgt: "#8b5cf6",    // violet
  coal: "#6b7280",        // slate
  nuclear: "#10b981",     // emerald
  other: "#f97316",       // orange
};

// ── Country defaults ──────────────────────────────────────────────────────────

/**
 * Default generation scenario starting shares (must sum to 1.0).
 * These are illustrative defaults for the left-panel sliders, not tied to any
 * real country's current generation mix.
 */
export const DEFAULT_SHARES = {
  solar: 0.15,
  wind_onshore: 0.10,
  gas_ccgt: 0.30,
  coal: 0.25,
  nuclear: 0.18,
  other: 0.02,
} as const;

/**
 * Default installed capacities in GW for the left-panel inputs.
 * These are capacity inputs, not constrained shares; served generation shares
 * and capacity shares are calculated from hourly dispatch output.
 */
export const DEFAULT_CAPACITIES_GW = {
  solar: 70,
  wind_onshore: 31,
  gas_ccgt: 27,
  coal: 24,
  nuclear: 14,
  other: 5,
} as const;

/** Default carbon price ($/tCO₂) shown in the sidebar slider on first load. */
export const DEFAULT_CARBON_PRICE_USD_TCO2 = 40;

/** Default EV penetration fraction (0 = no EVs, 0.5 = 50 % of fleet electrified). */
export const DEFAULT_EV_PENETRATION = 0;
