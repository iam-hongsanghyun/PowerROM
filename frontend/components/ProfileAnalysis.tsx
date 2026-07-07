"use client";

import { LoadDurationCurveChart } from "@/components/charts/LoadDurationCurveChart";
import type { CalculateResponse, Capacities, DispatchResponse, Shares } from "@/lib/api";

// ─── Types ───────────────────────────────────────────────────────────────────

interface Props {
  result: CalculateResponse | null;
  dispatchResult: DispatchResponse | null;
  isDispatchLoading: boolean;
  essCostUsdKwh: number;
  shares: Shares;
  capacities: Capacities;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function SummaryCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-[0.14em] text-slate-400">{label}</div>
      <div className="mt-1.5 text-xl font-semibold text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

function numericBreakdownValue(result: CalculateResponse, generator: string, key: string): number {
  const value = result.lcoe_by_generator[generator]?.[key];
  return typeof value === "number" ? value : 0;
}

// ─── Main component ───────────────────────────────────────────────────────────

export function ProfileAnalysis({
  result,
  dispatchResult,
  isDispatchLoading,
  essCostUsdKwh,
  shares,
  capacities,
}: Props) {
  if (!result) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-slate-400">
        Waiting for first calculation…
      </div>
    );
  }

  const currentVrePct = (shares.solar + shares.wind_onshore) * 100;
  const servedVrePct = (
    numericBreakdownValue(result, "solar", "realized_share")
    + numericBreakdownValue(result, "wind_onshore", "realized_share")
  ) * 100;

  return (
    <div className="space-y-6">
      <LoadDurationCurveChart
        ldc={dispatchResult?.ldc ?? result.ldc ?? null}
        dispatch={dispatchResult?.dispatch ?? result.dispatch ?? null}
        loading={isDispatchLoading}
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <SummaryCard
          label="VRE Capacity Share"
          value={`${currentVrePct.toFixed(0)}%`}
          sub={`Solar ${capacities.solar.toFixed(0)} GW + Wind ${capacities.wind_onshore.toFixed(0)} GW`}
        />
        <SummaryCard
          label="VRE Served Share"
          value={`${servedVrePct.toFixed(0)}%`}
          sub="Share of annual served generation"
        />
        <SummaryCard
          label="System LCOE"
          value={`$${result.system_lcoe.toFixed(1)}/MWh`}
          sub={`Annual cost $${result.annual_system_cost_usd_billion.toFixed(1)}B/yr`}
        />
        <SummaryCard
          label="Emission Intensity"
          value={`${(result.emission_intensity * 1000).toFixed(0)} gCO₂/kWh`}
          sub={`${result.annual_emissions_mtco2.toFixed(1)} MtCO₂/yr`}
        />
        <SummaryCard
          label="Storage"
          value={`${result.ess_requirement_gwh.toFixed(0)} GWh`}
          sub={`${result.ess_requirement_gw.toFixed(1)} GW · $${essCostUsdKwh}/kWh`}
        />
      </div>
    </div>
  );
}
