"use client";

import { X } from "lucide-react";
import { type CalculateResponse, type GeneratorKey } from "@/lib/api";

const GENERATOR_LABELS: Record<GeneratorKey, string> = {
  solar: "Solar",
  wind_onshore: "Wind (Onshore)",
  gas_ccgt: "Gas CCGT",
  coal: "Coal",
  nuclear: "Nuclear",
  other: "Other",
};

const COST_COMPONENTS = [
  { key: "capex", label: "CAPEX" },
  { key: "fixed_opex", label: "Fixed O&M" },
  { key: "variable_opex", label: "Variable O&M" },
  { key: "fuel", label: "Fuel" },
  { key: "carbon", label: "Carbon" },
  { key: "ess", label: "ESS" },
] as const;

interface Props {
  response: CalculateResponse;
  onClose: () => void;
}

export function GeneratorDetailView({ response, onClose }: Props) {
  const total = response.system_lcoe;

  const generators = Object.entries(response.shares)
    .filter(([, share]) => share > 0.001)
    .sort(([, a], [, b]) => b - a);

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-[2rem] border border-slate-200 bg-white shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)]">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-5 py-4">
        <h3 className="text-base font-semibold text-slate-900">Mix Detail</h3>
        <button
          onClick={onClose}
          className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          aria-label="Close detail panel"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
        {/* Generator Mix */}
        <section>
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
            Generator Mix
          </h4>
          <div className="space-y-2">
            {generators.map(([gen, share]) => (
              <div key={gen} className="flex items-center gap-3">
                <div className="w-28 shrink-0 text-sm text-slate-600">
                  {GENERATOR_LABELS[gen as GeneratorKey] ?? gen}
                </div>
                <div className="relative flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
                  <div
                    className="absolute inset-y-0 left-0 rounded-full bg-sky-500"
                    style={{ width: `${(share * 100).toFixed(1)}%` }}
                  />
                </div>
                <div className="w-12 shrink-0 text-right text-sm font-medium text-slate-700">
                  {(share * 100).toFixed(1)}%
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* System Metrics */}
        <section>
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
            System Metrics
          </h4>
          <div className="grid grid-cols-2 gap-3">
            <MetricCard label="System LCOE" value={`$${response.system_lcoe.toFixed(1)}`} unit="/MWh" />
            <MetricCard
              label="Emissions"
              value={(response.emission_intensity * 1000).toFixed(0)}
              unit="g CO2/kWh"
            />
            <MetricCard
              label="Annual Cost"
              value={`$${response.annual_system_cost_usd_billion.toFixed(1)}`}
              unit="billion/yr"
            />
            <MetricCard
              label="Annual Emissions"
              value={response.annual_emissions_mtco2.toFixed(1)}
              unit="MtCO2/yr"
            />
            <MetricCard
              label="ESS Power"
              value={response.ess_requirement_gw.toFixed(1)}
              unit="GW"
            />
            <MetricCard
              label="ESS Energy"
              value={response.ess_requirement_gwh.toFixed(0)}
              unit="GWh"
            />
          </div>
        </section>

        {/* Cost Breakdown */}
        <section>
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
            Cost Breakdown ($/MWh)
          </h4>
          <div className="overflow-hidden rounded-xl border border-slate-100">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  <th className="px-3 py-2 text-left font-medium text-slate-500">Component</th>
                  <th className="px-3 py-2 text-right font-medium text-slate-500">$/MWh</th>
                  <th className="px-3 py-2 text-right font-medium text-slate-500">Share</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {COST_COMPONENTS.map(({ key, label }) => {
                  const value = response.stack_components[key] ?? 0;
                  if (value < 0.01) return null;
                  return (
                    <tr key={key}>
                      <td className="px-3 py-2 text-slate-600">{label}</td>
                      <td className="px-3 py-2 text-right font-mono text-slate-800">
                        {value.toFixed(2)}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-500">
                        {total > 0 ? ((value / total) * 100).toFixed(1) : "0.0"}%
                      </td>
                    </tr>
                  );
                })}
                <tr className="border-t border-slate-200 bg-slate-50 font-semibold">
                  <td className="px-3 py-2 text-slate-800">Total</td>
                  <td className="px-3 py-2 text-right font-mono text-slate-900">
                    {total.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-700">100%</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        {/* Per-Generator Breakdown */}
        <section>
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
            Per-Generator LCOE
          </h4>
          <div className="space-y-2">
            {generators.map(([gen]) => {
              const genData = response.lcoe_by_generator[gen];
              if (!genData) return null;
              const genLcoe = Number(genData.total_lcoe ?? 0);
              const genEmission = Number(genData.emission_intensity_tco2_mwh ?? 0);
              return (
                <div
                  key={gen}
                  className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-700">
                      {GENERATOR_LABELS[gen as GeneratorKey] ?? gen}
                    </span>
                    <span className="text-sm font-semibold text-slate-900">
                      ${genLcoe.toFixed(1)}/MWh
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {(genEmission * 1000).toFixed(0)} gCO2/kWh
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}

function MetricCard({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-3">
      <div className="text-xs uppercase tracking-[0.16em] text-slate-500">{label}</div>
      <div className="mt-1.5 text-lg font-semibold text-slate-900">
        {value} <span className="text-xs font-normal text-slate-500">{unit}</span>
      </div>
    </div>
  );
}
