"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Play } from "lucide-react";
import * as Tabs from "@radix-ui/react-tabs";

import { ControlPanel, type StorageInput } from "@/components/ControlPanel";
import { ScenarioSettings } from "@/components/ScenarioSettings";
import { DemandProfileEditor, DEFAULT_DEMAND_PROFILE, type DemandProfile } from "@/components/DemandProfileEditor";
import { ShareSliders } from "@/components/ShareSliders";
import { ParametersTab } from "@/components/ParametersTab";
import { ProfileAnalysis } from "@/components/ProfileAnalysis";
import {
  calculateSystem,
  dispatchSystem,
  fetchCountries,
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
// Values must stay in sync with the JSON profiles in backend/data/country_profiles/.
const FALLBACK_COUNTRIES: CountrySummary[] = [
  {
    code: "KR",
    name: "South Korea",
    annual_generation_twh: 595,
    discount_rate: 0.05,
    generators: ["solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "other"],
    sources: [],
  },
  {
    code: "AU",
    name: "Australia",
    annual_generation_twh: 273,
    discount_rate: 0.05,
    generators: ["solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "other"],
    sources: [],
  },
  {
    code: "JP",
    name: "Japan",
    annual_generation_twh: 988,
    discount_rate: 0.05,
    generators: ["solar", "wind_onshore", "gas_ccgt", "coal", "nuclear", "other"],
    sources: [],
  },
];

const INITIAL_COUNTRY = "KR";
// Merit-order panel display order (top → bottom), reversed to peaker-first.
// Display only — dispatch order is computed from marginal cost in the backend.
// Must match ALL_GENERATOR_KEYS in @/lib/constants.
const GENERATOR_KEYS = ["other", "gas_ccgt", "coal", "nuclear", "wind_onshore", "solar"] as const;

function capacityShares(capacities: Capacities): Shares {
  const total = Object.values(capacities).reduce((sum, value) => sum + Math.max(0, value), 0);
  if (total <= 0) {
    return {
      solar: 0,
      wind_onshore: 0,
      gas_ccgt: 0,
      coal: 0,
      nuclear: 0,
      other: 0,
    };
  }
  return {
    solar: Math.max(0, capacities.solar) / total,
    wind_onshore: Math.max(0, capacities.wind_onshore) / total,
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
    gas_ccgt: String(capacities.gas_ccgt),
    coal: String(capacities.coal),
    nuclear: String(capacities.nuclear),
    other: String(capacities.other),
  };
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
  const [capacities, setCapacities] = useState<Capacities>({ ...DEFAULT_CAPACITIES_GW });
  const [capacityInputs, setCapacityInputs] = useState<Record<(typeof GENERATOR_KEYS)[number], string>>(
    capacityInputDefaults(DEFAULT_CAPACITIES_GW),
  );
  const [generatorOrder, setGeneratorOrder] = useState<GeneratorKey[]>([...GENERATOR_KEYS]);
  // Capacity expansion: which generators (or "storage") the solver may grow to meet 100% load.
  const [expandable, setExpandable] = useState<Set<string>>(new Set());
  const [meetFullLoad, setMeetFullLoad] = useState(false);
  const [carbonPrice, setCarbonPrice] = useState(DEFAULT_CARBON_PRICE_USD_TCO2);
  // Renewable-target (RPS) policy lever: share target (0 = off) + shortfall penalty.
  const [rpsTarget, setRpsTarget] = useState(0);
  const [rpsPenalty, setRpsPenalty] = useState(50);
  // Clean-energy subsidy (solar + wind + nuclear): ITC (0–1 capex) + PTC ($/MWh).
  const [subsidyItc, setSubsidyItc] = useState(0);
  const [subsidyPtc, setSubsidyPtc] = useState(0);
  const [fuelImportTariff, setFuelImportTariff] = useState(0);
  const [evPenetration, setEvPenetration] = useState(DEFAULT_EV_PENETRATION);
  const [annualDemandTwh, setAnnualDemandTwh] = useState(
    FALLBACK_COUNTRIES.find((c) => c.code === INITIAL_COUNTRY)?.annual_generation_twh ?? 595,
  );
  // User-set storage, dispatched endogenously (energy = power × duration). Illustrative defaults.
  const [storage, setStorage] = useState<StorageInput>({
    shortPowerGw: 20,
    longPowerGw: 5,
  });
  // Visual demand profile (12 monthly + 24 hourly), always sent as demand_monthly/demand_daily.
  const [demandProfile, setDemandProfile] = useState<DemandProfile>({
    monthly: [...DEFAULT_DEMAND_PROFILE.monthly],
    daily: [...DEFAULT_DEMAND_PROFILE.daily],
  });
  const [dispatchMode, setDispatchMode] = useState<DispatchMode>("parametric");
  const [weatherYears, setWeatherYears] = useState<number[]>([]);
  const [ensemble, setEnsemble] = useState<EnsembleConfig>({
    method: "jitter",
    n_samples: 5,
    sigma: 0.04,
    seed: 42,
  });
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

  function handleCountryChange(nextCountry: string) {
    setCountry(nextCountry);
    setCustomProfile(null); // clear edits when switching country
    const current = countries.find((item) => item.code === nextCountry);
    if (current) {
      setAnnualDemandTwh(current.annual_generation_twh);
    }
  }

  useEffect(() => {
    void fetchCountries()
      .then((response) => {
        setCountries(response.countries);
        const current = response.countries.find((item) => item.code === country);
        if (current) {
          setAnnualDemandTwh(current.annual_generation_twh);
        }
      })
      .catch(() => {
        setCountries(FALLBACK_COUNTRIES);
      });
  }, [country]);

  function buildCustomParams(): Record<string, unknown> {
    return (customProfile ?? {}) as Record<string, unknown>;
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
    const essPayload = {
      // Duration comes from the profile (Parameters -> ESS); only power is set here.
      ess_short_power_gw: storage.shortPowerGw,
      ess_long_power_gw: storage.longPowerGw,
      demand_monthly: demandProfile.monthly,
      demand_daily: demandProfile.daily,
      expandable: [...expandable],
      meet_full_load: meetFullLoad,
      rps_target_share: rpsTarget > 0 ? rpsTarget : null,
      rps_penalty_usd_mwh: rpsTarget > 0 ? rpsPenalty : null,
      subsidy_itc_pct: subsidyItc > 0 ? subsidyItc : null,
      subsidy_ptc_usd_mwh: subsidyPtc > 0 ? subsidyPtc : null,
      fuel_import_tariff_pct: fuelImportTariff > 0 ? fuelImportTariff : null,
    };

    try {
      const [calculation, dispatch] = await Promise.all([
        calculateSystem({
          country,
          capacities_gw: nextCapacities,
          carbon_price: carbonPrice,
          ev_penetration: evPenetration,
          annual_demand_twh: annualDemandTwh,
          custom_params,
          dispatch_mode: dispatchMode,
          weather_years: weatherYears.length ? weatherYears : null,
          ...essPayload,
        }),
        dispatchSystem({
          country,
          capacities_gw: nextCapacities,
          carbon_price: carbonPrice,
          ev_penetration: evPenetration,
          annual_demand_twh: annualDemandTwh,
          custom_params,
          dispatch_mode: dispatchMode,
          weather_years: weatherYears.length ? weatherYears : null,
          ensemble,
          ...essPayload,
        }),
      ]);
      setResult(calculation);
      setDispatchResult(dispatch);
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

  function toggleExpandable(key: string) {
    setExpandable((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(14,165,233,0.16),_transparent_28%),linear-gradient(135deg,_#f7fbff_0%,_#eef5ff_45%,_#fefcf6_100%)] text-slate-900">
      <div className="mx-auto max-w-[1600px] px-6 py-8 lg:px-10">
        <div className="mb-8 flex flex-col gap-3">
          <span className="w-fit rounded-full border border-sky-200 bg-white/80 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-sky-700">
            PowerROM
          </span>
          <h1 className="max-w-4xl text-4xl font-semibold tracking-tight text-slate-950 sm:text-5xl">
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
                generatorOrder={generatorOrder}
                calculatedShares={calculatedShares}
                expandable={expandable}
                meetFullLoad={meetFullLoad}
                addedCapacities={result?.expansion?.added_capacities_gw}
                expansionNote={result?.expansion?.note || undefined}
                onChange={handleCapacityInputChange}
                onOrderChange={setGeneratorOrder}
                onExpandableToggle={toggleExpandable}
                onMeetFullLoadChange={setMeetFullLoad}
              />
              <button
                type="button"
                onClick={runAnalysis}
                disabled={isAnalyzing}
                className="flex w-full items-center justify-center gap-2 rounded-2xl bg-slate-900 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
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

          <main>
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
                ].map((tab) => (
                  <Tabs.Trigger
                    key={tab.value}
                    value={tab.value}
                    className={[
                      "flex-1 rounded-xl px-4 py-2 text-sm font-medium transition",
                      "data-[state=active]:bg-slate-900 data-[state=active]:text-white",
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
            </Tabs.Root>
          </main>
        </div>
      </div>
    </div>
  );
}
