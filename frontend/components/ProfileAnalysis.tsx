"use client";

import { HourlyMixChart } from "@/components/charts/HourlyMixChart";
import { LoadDurationCurveChart } from "@/components/charts/LoadDurationCurveChart";
import type { Adequacy, CalculateResponse, Capacities, DispatchResponse, Shares } from "@/lib/api";

// Reference reliability standard (LOLE, hours/year) — "1 day in 10 years" ≈ 2.4 h/yr.
const LOLE_STANDARD_HOURS = 2.4;

/** Resource-adequacy readout: LOLE / LOLP / EUE with the shortfall tail across the ensemble. */
function AdequacyPanel({ adequacy }: { adequacy: Adequacy }) {
  const meets = adequacy.lole_hours <= LOLE_STANDARD_HOURS;
  const isBlock = adequacy.ensemble_method === "block_bootstrap";
  return (
    <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-800">Resource Adequacy</h3>
        <span
          className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
            meets ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"
          }`}
        >
          <span className={`h-2 w-2 rounded-full ${meets ? "bg-emerald-500" : "bg-rose-500"}`} />
          {meets ? "Meets" : "Below"} 1-day-in-10-yr standard (LOLE ≤ {LOLE_STANDARD_HOURS} h/yr)
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="LOLE" value={`${adequacy.lole_hours.toFixed(1)} h/yr`} sub={`LOLP ${(adequacy.lolp * 100).toFixed(2)}%`} />
        <Metric label="Expected unserved" value={`${(adequacy.eue_mwh / 1000).toFixed(1)} GWh/yr`} sub={`${(adequacy.eue_fraction * 1e6).toFixed(0)} ppm of demand`} />
        <Metric label="Shortfall years" value={`${(adequacy.loss_of_load_prob_annual * 100).toFixed(0)}%`} sub={`of ${adequacy.n_scenarios} sampled years`} />
        <Metric label="Worst-year unserved" value={`${(adequacy.unserved_mwh_max / 1000).toFixed(1)} GWh`} sub={`p99 ${(adequacy.unserved_mwh_p99 / 1000).toFixed(1)} GWh`} />
      </div>
      <p className="text-[11px] text-slate-400">
        From {adequacy.n_scenarios} jointly-sampled weather years ({adequacy.ensemble_method.replace("_", " ")}).
        {isBlock
          ? " Block bootstrap preserves multi-day droughts, so this tail is trustworthy."
          : " For a trustworthy adequacy tail, use the block-bootstrap sampler (Parameters → Ensemble)."}
      </p>
    </div>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-lg font-semibold tabular-nums text-slate-900">{value}</div>
      <div className="text-[11px] text-slate-500">{sub}</div>
    </div>
  );
}

// ─── Types ───────────────────────────────────────────────────────────────────

interface Props {
  result: CalculateResponse | null;
  dispatchResult: DispatchResponse | null;
  isDispatchLoading: boolean;
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

/** "[p10–p90]" range string when the ensemble produced a band, else empty. */
function bandRange(p10: number | null | undefined, p90: number | null | undefined, digits: number, scale = 1): string {
  if (p10 == null || p90 == null) return "";
  return `[${(p10 * scale).toFixed(digits)}–${(p90 * scale).toFixed(digits)}]`;
}

// ─── Main component ───────────────────────────────────────────────────────────

export function ProfileAnalysis({
  result,
  dispatchResult,
  isDispatchLoading,
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
      {result.rps ? (
        <div
          className={`flex items-center gap-2 rounded-2xl border px-4 py-2.5 text-sm ${
            result.rps.met
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-rose-200 bg-rose-50 text-rose-800"
          }`}
        >
          <span className={`h-2.5 w-2.5 rounded-full ${result.rps.met ? "bg-emerald-500" : "bg-rose-500"}`} />
          <span className="font-semibold">Renewable target {(result.rps.target_share * 100).toFixed(0)}%</span>
          <span>
            {result.rps.met ? "met" : "not met"} — achieved {(result.rps.achieved_share * 100).toFixed(0)}%
            {!result.rps.met && result.rps.penalty_lcoe > 0
              ? ` · +$${result.rps.penalty_lcoe.toFixed(1)}/MWh REC penalty`
              : ""}
          </span>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
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
          sub={
            bandRange(result.system_lcoe_p10, result.system_lcoe_p90, 1)
              ? `${bandRange(result.system_lcoe_p10, result.system_lcoe_p90, 1)} p10–p90 · $${result.annual_system_cost_usd_billion.toFixed(1)}B/yr`
              : `Annual cost $${result.annual_system_cost_usd_billion.toFixed(1)}B/yr`
          }
        />
        <SummaryCard
          label="Emission Intensity"
          value={`${(result.emission_intensity * 1000).toFixed(0)} gCO₂/kWh`}
          sub={
            bandRange(result.emission_intensity_p10, result.emission_intensity_p90, 0, 1000)
              ? `${bandRange(result.emission_intensity_p10, result.emission_intensity_p90, 0, 1000)} p10–p90 · ${result.annual_emissions_mtco2.toFixed(1)} MtCO₂/yr`
              : `${result.annual_emissions_mtco2.toFixed(1)} MtCO₂/yr`
          }
        />
        <SummaryCard
          label="Storage"
          value={`${result.ess_requirement_gwh.toFixed(0)} GWh`}
          sub={`${result.ess_requirement_gw.toFixed(1)} GW`}
        />
        <SummaryCard
          label="Import Dependency"
          value={`${(result.import_dependency * 100).toFixed(0)}%`}
          sub="Generation from imported fuel"
        />
      </div>

      {result.adequacy && result.adequacy.n_scenarios > 1 ? (
        <AdequacyPanel adequacy={result.adequacy} />
      ) : null}

      <HourlyMixChart
        chronological={dispatchResult?.chronological ?? result.chronological ?? null}
        loading={isDispatchLoading}
      />

      <LoadDurationCurveChart
        ldc={dispatchResult?.ldc ?? result.ldc ?? null}
        dispatch={dispatchResult?.dispatch ?? result.dispatch ?? null}
        loading={isDispatchLoading}
      />
    </div>
  );
}
