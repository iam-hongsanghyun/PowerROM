"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Play } from "lucide-react";
import * as Tabs from "@radix-ui/react-tabs";

import { ControlPanel, type StorageInput } from "@/components/ControlPanel";
import { ScenarioSettings } from "@/components/ScenarioSettings";
import { PathwayPanel } from "@/components/PathwayPanel";
import { DemandProfileEditor, DEFAULT_DEMAND_PROFILE, type DemandProfile } from "@/components/DemandProfileEditor";
import { ShareSliders } from "@/components/ShareSliders";
import { ParametersTab } from "@/components/ParametersTab";
import { ProfileAnalysis } from "@/components/ProfileAnalysis";
import {
  calculateSystem,
  fetchCountries,
  sizeForAdequacy,
  sizeMixForAdequacy,
  type SizeForAdequacyResult,
  type SizeMixForAdequacyResult,
  type CalculateResponse,
  type Capacities,
  type CountryProfile,
  type CountrySummary,
  type DispatchMode,
  type DispatchResponse,
  type EnsembleConfig,
  type GeneratorKey,
  type Shares,
} from "@/lib/api";
import {
  DEFAULT_CAPACITIES_GW,
  DEFAULT_CARBON_PRICE_USD_TCO2,
  DEFAULT_EV_PENETRATION,
} from "@/lib/constants";

// Fallback country list shown while the API is loading or unavailable.
// Values are real Ember actuals and must stay in sync with backend/data/country_profiles/
// (regenerate both via backend/data/build_country_profiles.py).
const GENS = ["solar", "wind_onshore", "wind_offshore", "gas_ccgt", "coal", "nuclear", "other"];
const FALLBACK_COUNTRIES: CountrySummary[] = [
  {
    code: "KR",
    name: "South Korea",
    annual_generation_twh: 625.38,
    annual_demand_twh: 625.38,
    discount_rate: 0.055,
    generators: GENS,
    capacities_gw: { solar: 26.68, wind_onshore: 2.26, wind_offshore: 0.0, gas_ccgt: 50.26, coal: 41.19, nuclear: 26.05, other: 8.0 },
    shares: { solar: 0.0523, wind_onshore: 0.0054, wind_offshore: 0.0, gas_ccgt: 0.2852, coal: 0.3051, nuclear: 0.3018, other: 0.0502 },
    data_year: 2024,
    sources: [],
  },
  {
    code: "AU",
    name: "Australia",
    annual_generation_twh: 281.64,
    annual_demand_twh: 281.64,
    discount_rate: 0.06,
    generators: GENS,
    capacities_gw: { solar: 36.0, wind_onshore: 15.56, wind_offshore: 0.0, gas_ccgt: 26.28, coal: 22.83, nuclear: 0.0, other: 11.1 },
    shares: { solar: 0.1783, wind_onshore: 0.1162, wind_offshore: 0.0, gas_ccgt: 0.1743, coal: 0.4521, nuclear: 0.0, other: 0.079 },
    data_year: 2024,
    sources: [],
  },
  {
    code: "JP",
    name: "Japan",
    annual_generation_twh: 1016.39,
    annual_demand_twh: 1016.39,
    discount_rate: 0.055,
    generators: GENS,
    capacities_gw: { solar: 89.6, wind_onshore: 5.66, wind_offshore: 0.2, gas_ccgt: 81.48, coal: 55.25, nuclear: 33.08, other: 54.27 },
    shares: { solar: 0.0951, wind_onshore: 0.0108, wind_offshore: 0.0006, gas_ccgt: 0.3407, coal: 0.3219, nuclear: 0.0835, other: 0.1475 },
    data_year: 2024,
    sources: [],
  },
];

const INITIAL_COUNTRY = "KR";
// Merit-order panel display order (top → bottom), reversed to peaker-first.
// Display only — dispatch order is computed from marginal cost in the backend.
// Must match ALL_GENERATOR_KEYS in @/lib/constants.
const GENERATOR_KEYS = ["other", "gas_ccgt", "coal", "nuclear", "wind_offshore", "wind_onshore", "solar"] as const;

function capacityShares(capacities: Capacities): Shares {
  const total = Object.values(capacities).reduce((sum, value) => sum + Math.max(0, value), 0);
  if (total <= 0) {
    return {
      solar: 0,
      wind_onshore: 0,
      wind_offshore: 0,
      gas_ccgt: 0,
      coal: 0,
      nuclear: 0,
      other: 0,
    };
  }
  return {
    solar: Math.max(0, capacities.solar) / total,
    wind_onshore: Math.max(0, capacities.wind_onshore) / total,
    wind_offshore: Math.max(0, capacities.wind_offshore) / total,
    gas_ccgt: Math.max(0, capacities.gas_ccgt) / total,
    coal: Math.max(0, capacities.coal) / total,
    nuclear: Math.max(0, capacities.nuclear) / total,
    other: Math.max(0, capacities.other) / total,
  };
}

function capacityInputDefaults(capacities: Capacities): Record<(typeof GENERATOR_KEYS)[number], string> {
  return {
    solar: String(capacities.solar),
    wind_onshore: String(capacities.wind_onshore),
    wind_offshore: String(capacities.wind_offshore),
    gas_ccgt: String(capacities.gas_ccgt),
    coal: String(capacities.coal),
    nuclear: String(capacities.nuclear),
    other: String(capacities.other),
  };
}

// Real installed capacities (GW) for a country, from its Ember-sourced profile. Falls back to
// the generic defaults only if the API returns a country with no capacity data.
function capacitiesFromSummary(current: CountrySummary): Capacities {
  const src = current.capacities_gw ?? {};
  const caps: Capacities = {
    solar: src.solar ?? 0,
    wind_onshore: src.wind_onshore ?? 0,
    wind_offshore: src.wind_offshore ?? 0,
    gas_ccgt: src.gas_ccgt ?? 0,
    coal: src.coal ?? 0,
    nuclear: src.nuclear ?? 0,
    other: src.other ?? 0,
  };
  const total = Object.values(caps).reduce((sum, value) => sum + Math.max(0, value), 0);
  return total > 0 ? caps : { ...DEFAULT_CAPACITIES_GW };
}

const INITIAL_CAPACITIES = capacitiesFromSummary(
  FALLBACK_COUNTRIES.find((c) => c.code === INITIAL_COUNTRY) ?? FALLBACK_COUNTRIES[0],
);

// Scenario-lever defaults. Single source of truth so both the initial state and the
// per-country reset (handleCountryChange) start from identical values.
const DEFAULT_RPS_PENALTY_USD_MWH = 50;
const DEFAULT_STORAGE: StorageInput = { shortPowerGw: 20, longPowerGw: 5 };
// The illustrative storage default above is anchored to Korea (~625 TWh/yr). With the roster now
// spanning 1–9000 TWh grids, seed each country's storage proportionally to its demand so a small
// grid doesn't start with Korea-scale storage dominating its LCOE.
const STORAGE_SEED_ANCHOR_TWH = 625;

function storageSeedForDemand(demandTwh: number): StorageInput {
  const scale = demandTwh / STORAGE_SEED_ANCHOR_TWH;
  const round1 = (x: number) => Math.round(x * 10) / 10;
  return {
    shortPowerGw: round1(DEFAULT_STORAGE.shortPowerGw * scale),
    longPowerGw: round1(DEFAULT_STORAGE.longPowerGw * scale),
  };
}
const DEFAULT_ENSEMBLE: EnsembleConfig = { method: "jitter", n_samples: 5, sigma: 0.04, seed: 42 };

function emptyCfInputs(): Record<(typeof GENERATOR_KEYS)[number], string> {
  return { solar: "", wind_onshore: "", wind_offshore: "", gas_ccgt: "", coal: "", nuclear: "", other: "" };
}

// Parse the per-generator CF text inputs into a {generator: cf} map, keeping only entries with a
// valid fraction in [0, 1]; blank/invalid inputs leave that generator unconstrained.
function parseCfLimits(
  inputs: Record<(typeof GENERATOR_KEYS)[number], string>,
): Partial<Record<GeneratorKey, number>> {
  const out: Partial<Record<GeneratorKey, number>> = {};
  for (const key of GENERATOR_KEYS) {
    const raw = inputs[key]?.trim();
    if (!raw) continue;
    const value = Number(raw);
    if (Number.isFinite(value) && value >= 0 && value <= 1) out[key] = value;
  }
  return out;
}

function parseCapacityInputs(
  capacityInputs: Record<(typeof GENERATOR_KEYS)[number], string>,
): Capacities | null {
  const parsed = {} as Capacities;
  for (const key of GENERATOR_KEYS) {
    const raw = capacityInputs[key].trim();
    if (raw === "") {
      parsed[key] = 0;
      continue;
    }
    const value = Number(raw);
    if (!Number.isFinite(value)) return null;
    parsed[key] = value;
  }
  return parsed;
}

export function Dashboard() {
  const [countries, setCountries] = useState<CountrySummary[]>(FALLBACK_COUNTRIES);
  const [country, setCountry] = useState(INITIAL_COUNTRY);
  const [capacities, setCapacities] = useState<Capacities>({ ...INITIAL_CAPACITIES });
  const [capacityInputs, setCapacityInputs] = useState<Record<(typeof GENERATOR_KEYS)[number], string>>(
    capacityInputDefaults(INITIAL_CAPACITIES),
  );
  // Per-generator capacity-factor limits (0–1). Blank = unconstrained. min = must-run floor,
  // max = availability ceiling. Applied in the backend dispatch.
  const [minCfInputs, setMinCfInputs] = useState<Record<(typeof GENERATOR_KEYS)[number], string>>(emptyCfInputs());
  const [maxCfInputs, setMaxCfInputs] = useState<Record<(typeof GENERATOR_KEYS)[number], string>>(emptyCfInputs());
  const [generatorOrder, setGeneratorOrder] = useState<GeneratorKey[]>([...GENERATOR_KEYS]);
  // Capacity expansion: which generators (or "storage") the solver may grow to meet 100% load.
  const [expandable, setExpandable] = useState<Set<string>>(new Set());
  const [meetFullLoad, setMeetFullLoad] = useState(false);
  const [carbonPrice, setCarbonPrice] = useState(DEFAULT_CARBON_PRICE_USD_TCO2);
  // Renewable-target (RPS) policy lever: share target (0 = off) + shortfall penalty.
  const [rpsTarget, setRpsTarget] = useState(0);
  const [rpsPenalty, setRpsPenalty] = useState(DEFAULT_RPS_PENALTY_USD_MWH);
  // Clean-energy subsidy (solar + wind + nuclear): ITC (0–1 capex) + PTC ($/MWh).
  const [subsidyItc, setSubsidyItc] = useState(0);
  const [subsidyPtc, setSubsidyPtc] = useState(0);
  const [fuelImportTariff, setFuelImportTariff] = useState(0);
  const [evPenetration, setEvPenetration] = useState(DEFAULT_EV_PENETRATION);
  const [annualDemandTwh, setAnnualDemandTwh] = useState(
    FALLBACK_COUNTRIES.find((c) => c.code === INITIAL_COUNTRY)?.annual_generation_twh ?? 595,
  );
  // User-set storage, dispatched endogenously (energy = power × duration). Illustrative defaults.
  const [storage, setStorage] = useState<StorageInput>({ ...DEFAULT_STORAGE });
  // Visual demand profile (12 monthly + 24 hourly), always sent as demand_monthly/demand_daily.
  const [demandProfile, setDemandProfile] = useState<DemandProfile>({
    monthly: [...DEFAULT_DEMAND_PROFILE.monthly],
    daily: [...DEFAULT_DEMAND_PROFILE.daily],
  });
  // Default to real weather-year data (falls back to the parametric synthesizer per country
  // when no hourly file exists for the selected country).
  const [dispatchMode, setDispatchMode] = useState<DispatchMode>("data");
  const [weatherYears, setWeatherYears] = useState<number[]>([]);
  const [ensemble, setEnsemble] = useState<EnsembleConfig>({ ...DEFAULT_ENSEMBLE });
  const [useCustomParameters, setUseCustomParameters] = useState(false);
  // Holds the user's in-progress parameter edits from the Parameters tab.
  // Persists across tab switches; reset only on country change or explicit reset.
  const [customProfile, setCustomProfile] = useState<CountryProfile | null>(null);
  const [result, setResult] = useState<CalculateResponse | null>(null);
  const [dispatchResult, setDispatchResult] = useState<DispatchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isDispatchLoading, setIsDispatchLoading] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const shares = capacityShares(capacities);
  // Model-calculated generation share (energy share) per generator from the last run,
  // distinct from the capacity share the user types in. Undefined until first run.
  const calculatedShares = result?.dispatch
    ? Object.fromEntries(
        Object.entries(result.dispatch.metrics.realized_share).map(([key, band]) => [key, band.median]),
      )
    : undefined;

  // Seed the left-panel demand + installed-capacity inputs from a country's real Ember profile.
  function applyCountryDefaults(current: CountrySummary) {
    // Prefer Ember's demand series; fall back to generation for profiles that predate the field.
    const demandTwh = current.annual_demand_twh ?? current.annual_generation_twh;
    setAnnualDemandTwh(demandTwh);
    setStorage(storageSeedForDemand(demandTwh));
    const caps = capacitiesFromSummary(current);
    setCapacities(caps);
    setCapacityInputs(capacityInputDefaults(caps));
  }

  // Switching country invalidates every value and result on screen, so start the new
  // country from a clean slate: re-seed the country-derived inputs from its profile and
  // reset every scenario lever, override, and cached result back to defaults.
  function handleCountryChange(nextCountry: string) {
    setCountry(nextCountry);

    // Country-derived inputs — reseeded from the new country's Ember profile.
    const current = countries.find((item) => item.code === nextCountry);
    if (current) {
      applyCountryDefaults(current); // annual demand + installed capacities
    }

    // Left-rail overrides back to unconstrained / default order.
    setMinCfInputs(emptyCfInputs());
    setMaxCfInputs(emptyCfInputs());
    setGeneratorOrder([...GENERATOR_KEYS]);
    setExpandable(new Set());
    setMeetFullLoad(false);

    // Scenario levers back to defaults.
    setCarbonPrice(DEFAULT_CARBON_PRICE_USD_TCO2);
    setRpsTarget(0);
    setRpsPenalty(DEFAULT_RPS_PENALTY_USD_MWH);
    setSubsidyItc(0);
    setSubsidyPtc(0);
    setFuelImportTariff(0);
    setEvPenetration(DEFAULT_EV_PENETRATION);
    // Storage is reseeded (demand-scaled) by applyCountryDefaults above.
    setDemandProfile({
      monthly: [...DEFAULT_DEMAND_PROFILE.monthly],
      daily: [...DEFAULT_DEMAND_PROFILE.daily],
    });
    setDispatchMode("data");
    setWeatherYears([]);
    setEnsemble({ ...DEFAULT_ENSEMBLE });
    setUseCustomParameters(false);
    setCustomProfile(null); // drop any Parameters-tab edits

    // Clear cached results/status so no stale numbers or expansion badges linger.
    setResult(null);
    setDispatchResult(null);
    setError(null);
    setIsAnalyzing(false);
    setIsDispatchLoading(false);
  }

  useEffect(() => {
    void fetchCountries()
      .then((response) => {
        setCountries(response.countries);
        const current = response.countries.find((item) => item.code === country);
        if (current) {
          applyCountryDefaults(current);
        }
      })
      .catch(() => {
        setCountries(FALLBACK_COUNTRIES);
      });
  }, [country]);

  function buildCustomParams(): Record<string, unknown> {
    return (customProfile ?? {}) as Record<string, unknown>;
  }

  function handleSizeForAdequacy(firmKey: string, targetHours: number): Promise<SizeForAdequacyResult> {
    // Sizing needs a real weather spread; force the correct sampler (block bootstrap) regardless
    // of the display ensemble so the LOLE reflects multi-day droughts. The left-rail min/max CF
    // limits are honoured throughout sizing (e.g. capping gas at 20% CF).
    const minCf = parseCfLimits(minCfInputs);
    const maxCf = parseCfLimits(maxCfInputs);
    return sizeForAdequacy({
      country,
      capacities_gw: capacities,
      firm_key: firmKey,
      lole_target_hours: targetHours,
      carbon_price: carbonPrice,
      annual_demand_twh: annualDemandTwh,
      ensemble: {
        ...ensemble,
        method: "block_bootstrap",
        n_samples: Math.max(12, ensemble.n_samples),
        block_days: ensemble.block_days ?? 14,
      },
      ess_short_power_gw: storage.shortPowerGw || null,
      ess_long_power_gw: storage.longPowerGw || null,
      min_cf: Object.keys(minCf).length ? minCf : undefined,
      max_cf: Object.keys(maxCf).length ? maxCf : undefined,
    });
  }

  function handleSizeMixForAdequacy(targetHours: number): Promise<SizeMixForAdequacyResult> {
    // Co-size the least-cost mix. Use the checked expandable set (defaulting to gas + storage if
    // nothing is checked), and force the block-bootstrap sampler for a drought-aware LOLE. The
    // left-rail min/max CF limits apply throughout (e.g. cap gas at 20% CF -> build clean+storage).
    const checked = [...expandable];
    const mix = checked.length ? checked : ["gas_ccgt", "storage"];
    const minCf = parseCfLimits(minCfInputs);
    const maxCf = parseCfLimits(maxCfInputs);
    return sizeMixForAdequacy({
      country,
      capacities_gw: capacities,
      expandable: mix,
      lole_target_hours: targetHours,
      carbon_price: carbonPrice,
      annual_demand_twh: annualDemandTwh,
      ensemble: {
        ...ensemble,
        method: "block_bootstrap",
        n_samples: Math.max(12, ensemble.n_samples),
        block_days: ensemble.block_days ?? 14,
      },
      ess_short_power_gw: storage.shortPowerGw || null,
      ess_long_power_gw: storage.longPowerGw || null,
      min_cf: Object.keys(minCf).length ? minCf : undefined,
      max_cf: Object.keys(maxCf).length ? maxCf : undefined,
    });
  }

  async function runAnalysis() {
    const nextCapacities = parseCapacityInputs(capacityInputs);
    if (!nextCapacities) {
      setError("Enter numeric GW values before running analysis.");
      return;
    }
    if (Object.values(nextCapacities).every((value) => value <= 0)) {
      setError("At least one generator capacity must be greater than 0 GW.");
      return;
    }

    setCapacities(nextCapacities);
    setError(null);
    setIsAnalyzing(true);
    setIsDispatchLoading(true);
    const custom_params = buildCustomParams();
    const minCf = parseCfLimits(minCfInputs);
    const maxCf = parseCfLimits(maxCfInputs);
    // Only send the demand shape when the user actually edited it — an untouched editor means
    // "use the country's own demand profile" (real ERA5 temperature-driven demand in data mode),
    // not the generic archetype the editor displays as its starting point.
    const demandEdited =
      demandProfile.monthly.some((v, i) => v !== DEFAULT_DEMAND_PROFILE.monthly[i]) ||
      demandProfile.daily.some((v, i) => v !== DEFAULT_DEMAND_PROFILE.daily[i]);
    const essPayload = {
      // Duration comes from the profile (Parameters -> ESS); only power is set here.
      ess_short_power_gw: storage.shortPowerGw,
      ess_long_power_gw: storage.longPowerGw,
      demand_monthly: demandEdited ? demandProfile.monthly : null,
      demand_daily: demandEdited ? demandProfile.daily : null,
      expandable: [...expandable],
      meet_full_load: meetFullLoad,
      rps_target_share: rpsTarget > 0 ? rpsTarget : null,
      rps_penalty_usd_mwh: rpsTarget > 0 ? rpsPenalty : null,
      subsidy_itc_pct: subsidyItc > 0 ? subsidyItc : null,
      subsidy_ptc_usd_mwh: subsidyPtc > 0 ? subsidyPtc : null,
      fuel_import_tariff_pct: fuelImportTariff > 0 ? fuelImportTariff : null,
      min_cf: Object.keys(minCf).length ? minCf : undefined,
      max_cf: Object.keys(maxCf).length ? maxCf : undefined,
    };

    try {
      // One request serves the whole dashboard: include_ldc adds the LDC + hourly payloads a
      // separate /dispatch call used to fetch by re-running the entire ensemble dispatch —
      // double the server work, and past the serverless 60 s ceiling on slow cold starts.
      const calculation = await calculateSystem({
        country,
        capacities_gw: nextCapacities,
        carbon_price: carbonPrice,
        ev_penetration: evPenetration,
        annual_demand_twh: annualDemandTwh,
        custom_params,
        dispatch_mode: dispatchMode,
        weather_years: weatherYears.length ? weatherYears : null,
        ensemble,
        include_ldc: true,
        ...essPayload,
      });
      setResult(calculation);
      setDispatchResult(null); // charts read the ldc/chronological on the calculate response
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Analysis failed.");
    } finally {
      setIsAnalyzing(false);
      setIsDispatchLoading(false);
    }
  }

  function handleCapacityInputChange(key: (typeof GENERATOR_KEYS)[number], value: string) {
    setCapacityInputs((prev) => ({ ...prev, [key]: value }));
  }

  function handleMinCfChange(key: (typeof GENERATOR_KEYS)[number], value: string) {
    setMinCfInputs((prev) => ({ ...prev, [key]: value }));
  }

  function handleMaxCfChange(key: (typeof GENERATOR_KEYS)[number], value: string) {
    setMaxCfInputs((prev) => ({ ...prev, [key]: value }));
  }

  function toggleExpandable(key: string) {
    setExpandable((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(1,116,190,0.10),_transparent_30%),linear-gradient(135deg,_#ffffff_0%,_#f3f8fd_52%,_#fff8e8_100%)] text-navy">
      <div className="mx-auto max-w-[1600px] px-6 py-8 lg:px-10">
        <div className="mb-8 flex flex-col gap-3">
          <div className="flex items-center gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/planit-logo.png" alt="PLANiT" className="h-8 w-auto" />
            <span className="rounded-full border border-brand/30 bg-white/80 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-brand">
              PowerROM
            </span>
          </div>
          <h1 className="max-w-4xl font-display text-4xl font-bold tracking-tight text-navy sm:text-5xl">
            Reduced-order electricity system analysis for policy decisions.
          </h1>
          <p className="max-w-3xl text-base leading-7 text-slate-600">
            Adjust the generation mix, carbon price, and storage assumptions to see system cost and emissions update instantly.
          </p>
        </div>

        <div className={`grid gap-6 ${sidebarCollapsed ? "" : "lg:grid-cols-[minmax(320px,30%)_1fr]"}`}>
          {!sidebarCollapsed && (
            <aside className="relative space-y-6 rounded-[2rem] border border-white/70 bg-white/85 p-6 shadow-[0_30px_120px_-56px_rgba(15,23,42,0.4)] backdrop-blur">
              <button
                onClick={() => setSidebarCollapsed(true)}
                title="Collapse sidebar"
                className="absolute right-4 top-4 rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
              >
                <ChevronLeft size={16} />
              </button>
              <ControlPanel
                countries={countries}
                country={country}
                storage={storage}
                storageExpandable={expandable.has("storage")}
                addedStorageGw={result?.expansion?.added_capacities_gw?.storage}
                addedStorageLongGw={result?.expansion?.added_capacities_gw?.storage_long}
                annualDemandTwh={annualDemandTwh}
                onCountryChange={handleCountryChange}
                onStorageChange={setStorage}
                onStorageExpandableToggle={() => toggleExpandable("storage")}
                onAnnualDemandChange={setAnnualDemandTwh}
              />
              <ShareSliders
                capacityInputs={capacityInputs}
                minCfInputs={minCfInputs}
                maxCfInputs={maxCfInputs}
                generatorOrder={generatorOrder}
                calculatedShares={calculatedShares}
                expandable={expandable}
                meetFullLoad={meetFullLoad}
                addedCapacities={result?.expansion?.added_capacities_gw}
                expansionNote={result?.expansion?.note || undefined}
                onChange={handleCapacityInputChange}
                onMinCfChange={handleMinCfChange}
                onMaxCfChange={handleMaxCfChange}
                onOrderChange={setGeneratorOrder}
                onExpandableToggle={toggleExpandable}
                onMeetFullLoadChange={setMeetFullLoad}
              />
              <button
                type="button"
                onClick={runAnalysis}
                disabled={isAnalyzing}
                className="flex w-full items-center justify-center gap-2 rounded-2xl bg-navy px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-navy-700 disabled:cursor-not-allowed disabled:bg-slate-400"
              >
                <Play size={16} />
                {isAnalyzing ? "Analysing..." : "Analyse"}
              </button>
              {error ? (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                  {error}
                </div>
              ) : null}
            </aside>
          )}

          <main className="min-w-0">
            {sidebarCollapsed && (
              <button
                onClick={() => setSidebarCollapsed(false)}
                className="mb-4 flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 shadow-sm transition hover:bg-slate-50"
              >
                <ChevronRight size={14} />
                Show controls
              </button>
            )}
            <Tabs.Root defaultValue="profile" className="space-y-4">
              {/* Tab bar */}
              <Tabs.List className="flex gap-1 rounded-2xl border border-slate-200 bg-white p-1.5 shadow-sm">
                {[
                  { value: "profile", label: "Profile" },
                  { value: "parameters", label: "Parameters" },
                  { value: "pathway", label: "Pathway" },
                ].map((tab) => (
                  <Tabs.Trigger
                    key={tab.value}
                    value={tab.value}
                    className={[
                      "flex-1 rounded-xl px-4 py-2 text-sm font-medium transition",
                      "data-[state=active]:bg-navy data-[state=active]:text-white",
                      "data-[state=inactive]:text-slate-500 data-[state=inactive]:hover:bg-slate-50",
                    ].join(" ")}
                  >
                    {tab.label}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>

              {/* Persistent status bar — visible on all tabs */}
              <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
                    Current Scenario
                  </h3>
                  <div className="flex flex-wrap gap-4 text-sm">
                    <span className="text-slate-500">
                      LCOE:{" "}
                      <strong className="text-slate-900">
                        ${result?.system_lcoe.toFixed(1) ?? "--"}/MWh
                      </strong>
                    </span>
                    <span className="text-slate-500">
                      Emissions:{" "}
                      <strong className="text-slate-900">
                        {result ? (result.emission_intensity * 1000).toFixed(0) : "--"} gCO₂/kWh
                      </strong>
                    </span>
                    <span className="text-slate-500">
                      ESS:{" "}
                      <strong className="text-slate-900">
                        {result?.ess_requirement_gwh.toFixed(0) ?? "--"} GWh
                        ({result?.ess_short_gwh.toFixed(0) ?? "--"} S + {result?.ess_long_gwh.toFixed(0) ?? "--"} L)
                      </strong>
                    </span>
                    <span className="text-slate-500">
                      Curtailment:{" "}
                      <strong className="text-slate-900">
                        {result ? (result.curtailment_rate * 100).toFixed(1) : "--"}%
                        ({result?.curtailed_twh.toFixed(0) ?? "--"} TWh/yr)
                      </strong>
                    </span>
                    <span className="text-slate-500">
                      Unserved:{" "}
                      <strong className="text-slate-900">
                        {result?.unserved_twh.toFixed(1) ?? "--"} TWh
                      </strong>
                    </span>
                    <span className="text-slate-500">
                      Annual Cost:{" "}
                      <strong className="text-slate-900">
                        ${result?.annual_system_cost_usd_billion.toFixed(1) ?? "--"}B
                      </strong>
                    </span>
                    {customProfile && (
                      <span className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                        Custom params active
                      </span>
                    )}
                    <span
                      className={
                        error
                          ? "font-medium text-rose-600"
                          : isAnalyzing
                            ? "font-medium text-amber-600"
                            : result
                              ? "font-medium text-emerald-600"
                              : "font-medium text-slate-400"
                      }
                    >
                      {error ? `Error: ${error}` : isAnalyzing ? "Analysing..." : result ? "Ready" : "Waiting"}
                    </span>
                  </div>
                </div>
              </div>

              {/* Profile Analysis tab */}
              <Tabs.Content value="profile">
                <ProfileAnalysis
                  result={result}
                  dispatchResult={dispatchResult}
                  isDispatchLoading={isDispatchLoading}
                  shares={shares}
                  capacities={capacities}
                  generatorOrder={generatorOrder}
                  onSizeForAdequacy={handleSizeForAdequacy}
                  onSizeMixForAdequacy={handleSizeMixForAdequacy}
                />
              </Tabs.Content>

              {/* Parameters tab — forceMount keeps state alive across tab switches */}
              <Tabs.Content value="parameters" forceMount className="data-[state=inactive]:hidden">
                <div className="space-y-4">
                  <ScenarioSettings
                    carbonPrice={carbonPrice}
                    rpsTarget={rpsTarget}
                    rpsPenalty={rpsPenalty}
                    subsidyItc={subsidyItc}
                    subsidyPtc={subsidyPtc}
                    fuelImportTariff={fuelImportTariff}
                    evPenetration={evPenetration}
                    dispatchMode={dispatchMode}
                    weatherYears={weatherYears}
                    ensemble={ensemble}
                    useCustomParameters={useCustomParameters}
                    onCarbonPriceChange={setCarbonPrice}
                    onRpsTargetChange={setRpsTarget}
                    onRpsPenaltyChange={setRpsPenalty}
                    onSubsidyItcChange={setSubsidyItc}
                    onSubsidyPtcChange={setSubsidyPtc}
                    onFuelImportTariffChange={setFuelImportTariff}
                    onEvPenetrationChange={setEvPenetration}
                    onDispatchModeChange={setDispatchMode}
                    onWeatherYearsChange={setWeatherYears}
                    onEnsembleChange={setEnsemble}
                    onUseCustomParametersChange={setUseCustomParameters}
                  />
                  <DemandProfileEditor profile={demandProfile} onChange={setDemandProfile} />
                  <ParametersTab country={country} onProfileEdited={setCustomProfile} />
                </div>
              </Tabs.Content>

              {/* Pathway tab — plan the fleet & carbon trajectory to a target year */}
              <Tabs.Content value="pathway">
                <PathwayPanel
                  country={country}
                  startCapacities={capacities}
                  startCarbonPrice={carbonPrice}
                  startDemandTwh={annualDemandTwh}
                  ensemble={ensemble}
                  storage={storage}
                />
              </Tabs.Content>
            </Tabs.Root>
          </main>
        </div>
      </div>
    </div>
  );
}
