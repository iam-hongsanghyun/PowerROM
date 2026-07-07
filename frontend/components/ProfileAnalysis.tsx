"use client";

import { useState } from "react";

import { HourlyMixChart } from "@/components/charts/HourlyMixChart";
import { LoadDurationCurveChart } from "@/components/charts/LoadDurationCurveChart";
import { GENERATOR_LABELS } from "@/lib/constants";
import type {
  Adequacy,
  CalculateResponse,
  Capacities,
  DispatchResponse,
  Shares,
  SizeForAdequacyResult,
  SizeMixForAdequacyResult,
} from "@/lib/api";

// Reference reliability standard (LOLE, hours/year) — "1 day in 10 years" ≈ 2.4 h/yr.
const LOLE_STANDARD_HOURS = 2.4;

const FIRM_OPTIONS: { key: string; label: string }[] = [
  { key: "gas_ccgt", label: "Gas CCGT" },
  { key: "nuclear", label: "Nuclear" },
  { key: "coal", label: "Coal" },
];

/** Grow a firm resource (or the cheapest mix) to a reliability standard; show what's required. */
function SizeToStandard({
  onSize,
  onSizeMix,
}: {
  onSize: (firmKey: string, targetHours: number) => Promise<SizeForAdequacyResult>;
  onSizeMix?: (targetHours: number) => Promise<SizeMixForAdequacyResult>;
}) {
  const [firmKey, setFirmKey] = useState("gas_ccgt");
  const [target, setTarget] = useState(LOLE_STANDARD_HOURS);
  const [running, setRunning] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    setText(null);
    try {
      if (firmKey === "mix" && onSizeMix) {
        const r = await onSizeMix(target);
        const blend = Object.entries(r.added_capacities_gw)
          .map(([key, gw]) => `${GENERATOR_LABELS[key] ?? key.replace("_", " ")} +${gw.toFixed(1)}`)
          .join(", ");
        setText(
          Object.keys(r.added_capacities_gw).length === 0
            ? `Already meets the standard — no build needed (LOLE ${r.lole_hours.toFixed(1)} h/yr).`
            : `Cheapest mix: ${blend} — LOLE ${r.baseline_lole_hours.toFixed(1)} → ${r.lole_hours.toFixed(1)} h/yr, ` +
                `system LCOE $${r.system_lcoe.toFixed(1)}/MWh${r.met ? "" : " (standard not reached)"}.`,
        );
      } else {
        const r = await onSize(firmKey, target);
        const firmLabel = FIRM_OPTIONS.find((option) => option.key === firmKey)?.label ?? firmKey;
        setText(
          r.added_gw > 0
            ? `Need ${r.required_gw.toFixed(1)} GW ${firmLabel} (+${r.added_gw.toFixed(1)}) — LOLE ` +
                `${r.baseline_lole_hours.toFixed(1)} → ${r.lole_hours.toFixed(1)} h/yr, system LCOE ` +
                `$${r.system_lcoe.toFixed(1)}/MWh${r.met ? "" : " (standard not reached)"}.`
            : `Already meets the standard — no added ${firmLabel} needed (LOLE ${r.lole_hours.toFixed(1)} h/yr).`,
        );
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sizing failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-2 rounded-xl border border-slate-100 bg-slate-50 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
        <span>Size</span>
        <select
          value={firmKey}
          onChange={(event) => setFirmKey(event.target.value)}
          className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-slate-900 outline-none focus:border-slate-400"
        >
          {FIRM_OPTIONS.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
          {onSizeMix ? <option value="mix">Cheapest mix</option> : null}
        </select>
        <span>to LOLE ≤</span>
        <input
          type="number"
          min={0}
          step={0.1}
          value={target}
          onChange={(event) => setTarget(Math.max(0, Number(event.target.value)))}
          className="w-16 rounded-lg border border-slate-200 bg-white px-2 py-1 text-right tabular-nums text-slate-900 outline-none focus:border-slate-400"
        />
        <span>h/yr</span>
        <button
          type="button"
          onClick={run}
          disabled={running}
          className="rounded-lg bg-navy px-3 py-1 font-medium text-white transition hover:bg-navy-700 disabled:opacity-50"
        >
          {running ? "Sizing…" : "Size"}
        </button>
      </div>
      {error ? <p className="text-[11px] text-rose-600">{error}</p> : null}
      {text ? <p className="text-[11px] text-slate-600">{text}</p> : null}
    </div>
  );
}

/** Resource-adequacy readout: LOLE / LOLP / EUE with the shortfall tail across the ensemble. */
function AdequacyPanel({
  adequacy,
  onSize,
  onSizeMix,
}: {
  adequacy: Adequacy;
  onSize?: (firmKey: string, targetHours: number) => Promise<SizeForAdequacyResult>;
  onSizeMix?: (targetHours: number) => Promise<SizeMixForAdequacyResult>;
}) {
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
      {onSize ? <SizeToStandard onSize={onSize} onSizeMix={onSizeMix} /> : null}
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
  onSizeForAdequacy?: (firmKey: string, targetHours: number) => Promise<SizeForAdequacyResult>;
  onSizeMixForAdequacy?: (targetHours: number) => Promise<SizeMixForAdequacyResult>;
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
  onSizeForAdequacy,
  onSizeMixForAdequacy,
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
        <AdequacyPanel
          adequacy={result.adequacy}
          onSize={onSizeForAdequacy}
          onSizeMix={onSizeMixForAdequacy}
        />
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
