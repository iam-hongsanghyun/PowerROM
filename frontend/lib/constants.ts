/**
 * Shared front-end constants for generator metadata and country defaults.
 *
 * Keep these in sync with:
 *   backend/core/lcoe_engine.py   — VRE_GENERATORS
 *   backend/data/country_profiles — ESS capex and country names
 */

// ── Generator registry ────────────────────────────────────────────────────────

/** Keys that identify intermittent (variable renewable) generators. */
export const VRE_GENERATOR_KEYS = new Set(["solar", "wind_onshore"]);

/** All generator keys in preferred display order. */
export const ALL_GENERATOR_KEYS = [
  "solar",
  "wind_onshore",
  "gas_ccgt",
  "coal",
  "nuclear",
  "other",
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
 * Default short-duration ESS capex ($/kWh) per country.
 * Must stay in sync with `ess.short_dur.capex_usd_kwh` in each country JSON profile.
 */
export const COUNTRY_ESS_CAPEX: Record<string, number> = {
  KR: 280, // South Korea — backend/data/country_profiles/KR.json
  AU: 260, // Australia   — backend/data/country_profiles/AU.json
  JP: 290, // Japan       — backend/data/country_profiles/JP.json
};

/** Fallback ESS capex when no country-specific value is registered. */
export const FALLBACK_ESS_CAPEX_USD_KWH = 280;

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

/** Default carbon price ($/tCO₂) shown in the sidebar slider on first load. */
export const DEFAULT_CARBON_PRICE_USD_TCO2 = 40;

/** Default EV penetration fraction (0 = no EVs, 0.5 = 50 % of fleet electrified). */
export const DEFAULT_EV_PENETRATION = 0;
