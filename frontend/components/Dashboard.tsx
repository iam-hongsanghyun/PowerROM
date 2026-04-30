"use client";

import { useEffect, useState, useTransition } from "react";
import dynamic from "next/dynamic";

import { ControlPanel } from "@/components/ControlPanel";
import { ShareSliders } from "@/components/ShareSliders";
import { CompletenessReport } from "@/components/parameter/CompletenessReport";
import { ExcelUploader } from "@/components/parameter/ExcelUploader";
import {
  calculateSystem,
  fetchCountries,
  fitCurve,
  type CalculateResponse,
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

const SystemLcoeChart = dynamic(
  () => import("@/components/charts/SystemLcoeChart").then((mod) => mod.SystemLcoeChart),
  { ssr: false },
);
const EmissionIntensityChart = dynamic(
  () =>
    import("@/components/charts/EmissionIntensityChart").then(
      (mod) => mod.EmissionIntensityChart,
    ),
  { ssr: false },
);
const CostBreakdownChart = dynamic(
  () => import("@/components/charts/CostBreakdownChart").then((mod) => mod.CostBreakdownChart),
  { ssr: false },
);
const TradeoffChart = dynamic(
  () => import("@/components/charts/TradeoffChart").then((mod) => mod.TradeoffChart),
  { ssr: false },
);

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
  const [useCustomParameters, setUseCustomParameters] = useState(false);
  const [result, setResult] = useState<CalculateResponse | null>(null);
  const [validation, setValidation] = useState<Record<string, Record<string, string | number | null>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function handleCountryChange(nextCountry: string) {
    setCountry(nextCountry);
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
    startTransition(() => {
      void calculateSystem({
        country,
        shares,
        carbon_price: carbonPrice,
        ev_penetration: evPenetration,
        annual_demand_twh: annualDemandTwh,
      })
        .then((response) => {
          setResult(response);
          setError(null);
        })
        .catch((requestError: Error) => setError(requestError.message));
    });
  }, [annualDemandTwh, carbonPrice, country, evPenetration, shares]);

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

  const currentVreShare = shares.solar + shares.wind_onshore;

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

        <div className="grid gap-6 lg:grid-cols-[minmax(320px,30%)_1fr]">
          <aside className="space-y-6 rounded-[2rem] border border-white/70 bg-white/85 p-6 shadow-[0_30px_120px_-56px_rgba(15,23,42,0.4)] backdrop-blur">
            <ControlPanel
              countries={countries}
              country={country}
              carbonPrice={carbonPrice}
              evPenetration={evPenetration}
              annualDemandTwh={annualDemandTwh}
              useCustomParameters={useCustomParameters}
              onCountryChange={handleCountryChange}
              onCarbonPriceChange={setCarbonPrice}
              onEvPenetrationChange={setEvPenetration}
              onAnnualDemandChange={setAnnualDemandTwh}
              onUseCustomParametersChange={setUseCustomParameters}
            />
            <ShareSliders shares={shares} onChange={setShares} />
            {result?.data_quality.share_normalized ? (
              <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                Shares were normalized to sum to 100%.
              </div>
            ) : null}
            {error ? (
              <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                {error}
              </div>
            ) : null}
          </aside>

          <main className="space-y-6">
            <div className="grid gap-6 xl:grid-cols-2">
              <SystemLcoeChart data={result?.curve_data ?? []} selectedVreShare={currentVreShare} />
              <EmissionIntensityChart data={result?.curve_data ?? []} selectedVreShare={currentVreShare} />
              <CostBreakdownChart lcoeByGenerator={result?.lcoe_by_generator ?? {}} />
              <TradeoffChart
                data={result?.curve_data ?? []}
                currentPoint={{
                  lcoe: result?.system_lcoe ?? 0,
                  emission: result?.emission_intensity ?? 0,
                }}
              />
            </div>

            <div className="grid gap-6 xl:grid-cols-[minmax(320px,0.45fr)_1fr]">
              <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)]">
                <h3 className="text-base font-semibold text-slate-900">Scenario Summary</h3>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-500">System LCOE</div>
                    <div className="mt-2 text-2xl font-semibold">${result?.system_lcoe.toFixed(1) ?? "--"}</div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Emissions</div>
                    <div className="mt-2 text-2xl font-semibold">
                      {result ? (result.emission_intensity * 1000).toFixed(0) : "--"} g/kWh
                    </div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-500">ESS Requirement</div>
                    <div className="mt-2 text-2xl font-semibold">
                      {result?.ess_requirement_gwh.toFixed(0) ?? "--"} GWh
                    </div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Annual Cost</div>
                    <div className="mt-2 text-2xl font-semibold">
                      ${result?.annual_system_cost_usd_billion.toFixed(1) ?? "--"}B
                    </div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Annual Emissions</div>
                    <div className="mt-2 text-2xl font-semibold">
                      {result?.annual_emissions_mtco2.toFixed(1) ?? "--"} MtCO2
                    </div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Status</div>
                    <div className="mt-2 text-2xl font-semibold">
                      {error ? "Backend issue" : isPending ? "Updating" : result ? "Ready" : "Waiting"}
                    </div>
                  </div>
                </div>
              </div>

              {useCustomParameters ? (
                <div className="grid gap-6 md:grid-cols-2">
                  <ExcelUploader onParsed={handleExcelPoints} />
                  <CompletenessReport components={validation} />
                </div>
              ) : (
                <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)]">
                  <h3 className="text-base font-semibold text-slate-900">Default Data Quality</h3>
                  <div className="mt-4 space-y-3 text-sm text-slate-600">
                    {(result?.data_quality.notes ?? []).map((note) => (
                      <p key={note}>{note}</p>
                    ))}
                    <p>Sources: {(result?.data_quality.sources ?? []).join(" · ")}</p>
                  </div>
                </div>
              )}
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}
