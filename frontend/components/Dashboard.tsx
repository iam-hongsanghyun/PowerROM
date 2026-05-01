"use client";

import { useEffect, useState, useTransition } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import dynamic from "next/dynamic";
import * as Tabs from "@radix-ui/react-tabs";

import { ControlPanel } from "@/components/ControlPanel";
import { ShareSliders } from "@/components/ShareSliders";
import { CompletenessReport } from "@/components/parameter/CompletenessReport";
import { ExcelUploader } from "@/components/parameter/ExcelUploader";
import { GeneratorMixPlotter } from "@/components/GeneratorMixPlotter";
import { ParametersTab } from "@/components/ParametersTab";
import { ProfileAnalysis } from "@/components/ProfileAnalysis";
import {
  calculateSystem,
  fetchCountries,
  fitCurve,
  type CalculateResponse,
  type CountryProfile,
  type CountrySummary,
  type Shares,
  validateGeneratorConfig,
} from "@/lib/api";

const DEFAULT_COUNTRIES: CountrySummary[] = [
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

const DEFAULT_SHARES: Shares = {
  solar: 0.15,
  wind_onshore: 0.1,
  gas_ccgt: 0.3,
  coal: 0.25,
  nuclear: 0.18,
  other: 0.02,
};

export function Dashboard() {
  const [countries, setCountries] = useState<CountrySummary[]>(DEFAULT_COUNTRIES);
  const [country, setCountry] = useState("KR");
  const [shares, setShares] = useState<Shares>(DEFAULT_SHARES);
  const [carbonPrice, setCarbonPrice] = useState(40);
  const [evPenetration, setEvPenetration] = useState(0);
  const [annualDemandTwh, setAnnualDemandTwh] = useState(595);
  const [essCostUsdKwh, setEssCostUsdKwh] = useState(280);
  const [useCustomParameters, setUseCustomParameters] = useState(false);
  // Holds the user's in-progress parameter edits from the Parameters tab.
  // Persists across tab switches; reset only on country change or explicit reset.
  const [customProfile, setCustomProfile] = useState<CountryProfile | null>(null);
  const [result, setResult] = useState<CalculateResponse | null>(null);
  const [validation, setValidation] = useState<Record<string, Record<string, string | number | null>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

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
        setCountries(DEFAULT_COUNTRIES);
      });
  }, [country]);

  useEffect(() => {
    // Build custom_params: merge the user's profile edits (if any) with the
    // sidebar ESS-cost slider. The sidebar always wins for ESS capex.
    const custom_params: Record<string, unknown> = customProfile
      ? ({
          ...customProfile,
          ess: {
            ...customProfile.ess,
            short_dur: {
              ...(customProfile.ess?.short_dur ?? {}),
              capex_usd_kwh: essCostUsdKwh,
            },
          },
        } as Record<string, unknown>)
      : { ess: { short_dur: { capex_usd_kwh: essCostUsdKwh } } };

    startTransition(() => {
      void calculateSystem({
        country,
        shares,
        carbon_price: carbonPrice,
        ev_penetration: evPenetration,
        annual_demand_twh: annualDemandTwh,
        custom_params,
      })
        .then((response) => {
          setResult(response);
          setError(null);
        })
        .catch((requestError: Error) => setError(requestError.message));
    });
  }, [annualDemandTwh, carbonPrice, country, customProfile, essCostUsdKwh, evPenetration, shares]);

  async function handleExcelPoints(points: Array<[number, number]>) {
    const fit = await fitCurve({ data_points: points, func_type: "linear" });
    const validationResult = await validateGeneratorConfig({
      generator_config: {
        capex_usd_kw: 900,
        opex_fixed_usd_kw_yr: 15,
        opex_var_usd_mwh: 0,
        lifetime_yr: 25,
        emission_factor_tco2_mwh: 0,
        cf_eff_func: { type: "linear", params: fit.params, r_squared: fit.r_squared },
        eta_func: { type: "constant", params: { a: 1 } },
        integration_cost_func: { type: "constant", params: { a: 0 } },
      },
    });
    setValidation(validationResult.components);
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
                carbonPrice={carbonPrice}
                essCostUsdKwh={essCostUsdKwh}
                evPenetration={evPenetration}
                annualDemandTwh={annualDemandTwh}
                useCustomParameters={useCustomParameters}
                onCountryChange={handleCountryChange}
                onCarbonPriceChange={setCarbonPrice}
                onEssCostChange={setEssCostUsdKwh}
                onEvPenetrationChange={setEvPenetration}
                onAnnualDemandChange={setAnnualDemandTwh}
                onUseCustomParametersChange={setUseCustomParameters}
              />
              <ShareSliders shares={shares} onChange={setShares} />
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
                  { value: "mix", label: "Mix" },
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
                          : isPending
                            ? "font-medium text-amber-600"
                            : result
                              ? "font-medium text-emerald-600"
                              : "font-medium text-slate-400"
                      }
                    >
                      {error ? `Error: ${error}` : isPending ? "Updating…" : result ? "Ready" : "Waiting"}
                    </span>
                  </div>
                </div>
              </div>

              {/* Profile Analysis tab */}
              <Tabs.Content value="profile">
                <ProfileAnalysis
                  result={result}
                  country={country}
                  carbonPrice={carbonPrice}
                  essCostUsdKwh={essCostUsdKwh}
                  shares={shares}
                  annualDemandTwh={annualDemandTwh}
                  evPenetration={evPenetration}
                />
              </Tabs.Content>

              {/* Mix Explorer tab */}
              <Tabs.Content value="mix" className="space-y-4">
                <GeneratorMixPlotter
                  country={country}
                  carbonPrice={carbonPrice}
                  essCostUsdKwh={essCostUsdKwh}
                  evPenetration={evPenetration}
                  annualDemandTwh={annualDemandTwh}
                  shares={shares}
                />

                {useCustomParameters && (
                  <div className="grid gap-6 md:grid-cols-2">
                    <ExcelUploader onParsed={handleExcelPoints} />
                    <CompletenessReport components={validation} />
                  </div>
                )}
              </Tabs.Content>

              {/* Parameters tab — forceMount keeps state alive across tab switches */}
              <Tabs.Content value="parameters" forceMount className="data-[state=inactive]:hidden">
                <ParametersTab
                  country={country}
                  onProfileEdited={setCustomProfile}
                />
              </Tabs.Content>
            </Tabs.Root>
          </main>
        </div>
      </div>
    </div>
  );
}
