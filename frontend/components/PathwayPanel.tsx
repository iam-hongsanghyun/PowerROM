"use client";

import { useState } from "react";

import type { Capacities, EnsembleConfig, GeneratorKey, PathwayStep } from "@/lib/api";
import { simulatePathway } from "@/lib/api";
import { GENERATOR_COLORS, GENERATOR_LABELS, STORAGE_COLOR } from "@/lib/constants";
import { InfoTip } from "@/components/InfoTip";
import type { StorageInput } from "@/components/ShareSliders";

const GENERATOR_KEYS = ["solar", "wind_onshore", "wind_offshore", "nuclear", "hydro", "coal", "gas_ccgt", "other"] as const;
const START_YEAR = 2025;

/** Four evenly-spaced milestone years from START_YEAR to the chosen end year. */
function milestoneYears(endYear: number): number[] {
  const span = endYear - START_YEAR;
  return [0, 1, 2, 3].map((i) => START_YEAR + Math.round((span * i) / 3));
}

/**
 * Planning-pathway panel: runs the model from today's fleet (the start) to a user-set end-of-horizon
 * mix, interpolating capacities (phase-out / build), an escalating carbon price, and demand growth,
 * then charts how system LCOE, emission intensity, and import dependency evolve to the target year.
 */
export function PathwayPanel({
  country,
  startCapacities,
  startCarbonPrice,
  startDemandTwh,
  ensemble,
  storage,
  minCf,
  maxCf,
}: {
  country: string;
  startCapacities: Capacities;
  startCarbonPrice: number;
  startDemandTwh: number;
  ensemble: EnsembleConfig;
  storage: StorageInput;
  // Per-generator CF limits from the left rail (parsed), so an edited firm ceiling carries into
  // the trajectory. Undefined/empty = fall back to the profile's default caps at each year.
  minCf?: Partial<Record<GeneratorKey, number>>;
  maxCf?: Partial<Record<GeneratorKey, number>>;
}) {
  const [endYear, setEndYear] = useState(2050);
  const [endCarbon, setEndCarbon] = useState(150);
  const [endDemand, setEndDemand] = useState(Math.round(startDemandTwh));
  const [target, setTarget] = useState<Capacities>({ ...startCapacities });
  // Capacity expansion: grow the checked resources to meet 100% load at each milestone year.
  const [meetFullLoad, setMeetFullLoad] = useState(false);
  const [expandable, setExpandable] = useState<Set<string>>(new Set());
  const [steps, setSteps] = useState<PathwayStep[] | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleExpandable(key: string) {
    setExpandable((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function run() {
    setIsRunning(true);
    setError(null);
    try {
      const response = await simulatePathway({
        country,
        start_capacities_gw: startCapacities,
        target_capacities_gw: target,
        years: milestoneYears(endYear),
        carbon_price_start: startCarbonPrice,
        carbon_price_end: endCarbon,
        annual_demand_twh_start: startDemandTwh,
        annual_demand_twh_end: endDemand,
        ensemble,
        ess_short_power_gw: storage.shortPowerGw || null,
        ess_long_power_gw: storage.longPowerGw || null,
        expandable: meetFullLoad ? [...expandable] : undefined,
        meet_full_load: meetFullLoad,
        min_cf: minCf && Object.keys(minCf).length ? minCf : undefined,
        max_cf: maxCf && Object.keys(maxCf).length ? maxCf : undefined,
      });
      setSteps(response.steps);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Pathway failed");
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-1.5">
        <h3 className="text-sm font-semibold text-slate-800">Planning Pathway</h3>
        <InfoTip text="Runs the model from today's fleet to a target-year mix, interpolating capacities (phase-out or build), an escalating carbon price, and demand growth — so you can see how cost, emissions, and import dependency evolve to the horizon." />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <label className="space-y-1 text-xs font-medium text-slate-600">
          Target year
          <select
            value={endYear}
            onChange={(event) => setEndYear(Number(event.target.value))}
            className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-slate-400"
          >
            {[2040, 2050, 2060].map((year) => (
              <option key={year} value={year}>
                {year}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs font-medium text-slate-600">
          End carbon ($/t)
          <input
            type="number"
            min={0}
            value={endCarbon}
            onChange={(event) => setEndCarbon(Math.max(0, Number(event.target.value)))}
            className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
          />
        </label>
        <label className="space-y-1 text-xs font-medium text-slate-600">
          End demand (TWh)
          <input
            type="number"
            min={0}
            value={endDemand}
            onChange={(event) => setEndDemand(Math.max(0, Number(event.target.value)))}
            className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
          />
        </label>
      </div>

      <div className="space-y-1.5">
        <div className="text-xs font-medium text-slate-600">
          {endYear} target capacity (GW) — set to 0 to phase out, check ⤢ to let it expand
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {GENERATOR_KEYS.map((key) => (
            <div key={key} className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs">
              <span className="flex items-center gap-1.5 text-slate-600">
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: GENERATOR_COLORS[key] }} />
                {GENERATOR_LABELS[key]}
              </span>
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  min={0}
                  value={target[key]}
                  onChange={(event) =>
                    setTarget((prev) => ({ ...prev, [key]: Math.max(0, Number(event.target.value)) }))
                  }
                  aria-label={`${GENERATOR_LABELS[key]} target capacity in GW`}
                  className="w-16 rounded-lg border border-slate-200 bg-white px-2 py-1 text-right tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
                />
                <input
                  type="checkbox"
                  checked={expandable.has(key)}
                  onChange={() => toggleExpandable(key)}
                  disabled={!meetFullLoad}
                  aria-label={`Expand ${GENERATOR_LABELS[key]} to meet load`}
                  title="Let the solver grow this generator to meet 100% load (enable 'Meet 100% load' first)"
                  className="h-3.5 w-3.5 rounded border-slate-300 disabled:opacity-40"
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="space-y-1.5">
        <label className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700">
          <span>
            Meet 100% load each year
            <span className="ml-1 text-[10px] text-slate-400">grow the checked (⤢) resources, cheapest-first</span>
          </span>
          <input
            type="checkbox"
            checked={meetFullLoad}
            onChange={(event) => setMeetFullLoad(event.target.checked)}
            className="h-4 w-4 rounded border-slate-300"
          />
        </label>
        <label className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: STORAGE_COLOR }} />
            Storage (short + long)
          </span>
          <input
            type="checkbox"
            checked={expandable.has("storage")}
            onChange={() => toggleExpandable("storage")}
            disabled={!meetFullLoad}
            aria-label="Expand storage to meet load"
            title="Let the solver grow storage to meet 100% load"
            className="h-3.5 w-3.5 rounded border-slate-300 disabled:opacity-40"
          />
        </label>
      </div>

      <button
        type="button"
        onClick={run}
        disabled={isRunning}
        className="w-full rounded-xl bg-navy px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-navy-700 disabled:opacity-50"
      >
        {isRunning ? "Running pathway..." : `Run pathway to ${endYear}`}
      </button>

      {error ? <p className="text-xs text-rose-600">{error}</p> : null}

      {steps ? <PathwayChart steps={steps} /> : null}
    </div>
  );
}

/** Emission-intensity bars with an overlaid system-LCOE line, plus a compact trajectory table. */
function PathwayChart({ steps }: { steps: PathwayStep[] }) {
  const width = 520;
  const height = 200;
  const padX = 44;
  const padY = 24;
  const plotW = width - padX * 2;
  const plotH = height - padY * 2;

  const intensities = steps.map((s) => s.emission_intensity * 1000); // gCO2/kWh
  const lcoes = steps.map((s) => s.system_lcoe);
  const maxIntensity = Math.max(1, ...intensities);
  const maxLcoe = Math.max(1, ...lcoes) * 1.15;
  const barW = (plotW / steps.length) * 0.5;

  const x = (i: number) => padX + (plotW * (i + 0.5)) / steps.length;
  const yBar = (v: number) => padY + plotH * (1 - v / maxIntensity);
  const yLine = (v: number) => padY + plotH * (1 - v / maxLcoe);

  const linePath = steps
    .map((s, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${yLine(s.system_lcoe).toFixed(1)}`)
    .join(" ");

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Pathway trajectory">
          {[0, 0.5, 1].map((f) => (
            <line
              key={f}
              x1={padX}
              x2={width - padX}
              y1={padY + plotH * f}
              y2={padY + plotH * f}
              stroke="#e2e8f0"
              strokeWidth={1}
            />
          ))}
          {steps.map((s, i) => {
            const v = s.emission_intensity * 1000;
            return (
              <g key={s.year}>
                <rect
                  x={x(i) - barW / 2}
                  y={yBar(v)}
                  width={barW}
                  height={padY + plotH - yBar(v)}
                  rx={3}
                  fill="#94a3b8"
                />
                <text x={x(i)} y={height - 6} textAnchor="middle" fontSize={11} fill="#64748b">
                  {s.year}
                </text>
                <text x={x(i)} y={yBar(v) - 4} textAnchor="middle" fontSize={9} fill="#64748b">
                  {v.toFixed(0)}
                </text>
              </g>
            );
          })}
          <path d={linePath} fill="none" stroke="#0ea5e9" strokeWidth={2.5} />
          {steps.map((s, i) => (
            <circle key={s.year} cx={x(i)} cy={yLine(s.system_lcoe)} r={3.5} fill="#0ea5e9" />
          ))}
        </svg>
      </div>

      <div className="flex flex-wrap gap-4 text-[11px] text-slate-500">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-slate-400" /> Emission intensity (gCO₂/kWh)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded bg-sky-500" /> System LCOE ($/MWh)
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs tabular-nums">
          <thead className="text-slate-400">
            <tr>
              <th className="py-1 pr-3 font-medium">Year</th>
              <th className="py-1 pr-3 font-medium">Carbon</th>
              <th className="py-1 pr-3 font-medium">LCOE</th>
              <th className="py-1 pr-3 font-medium">MtCO₂/yr</th>
              <th className="py-1 pr-3 font-medium">gCO₂/kWh</th>
              <th className="py-1 pr-3 font-medium">Import</th>
              <th className="py-1 pr-3 font-medium">Unserved</th>
              <th className="py-1 font-medium">Built +GW</th>
            </tr>
          </thead>
          <tbody className="text-slate-700">
            {steps.map((s) => {
              const added = s.added_capacities_gw ?? {};
              const addedTotal = Object.values(added).reduce((sum, v) => sum + v, 0);
              const addedLabel = Object.entries(added)
                .map(([key, v]) => `${GENERATOR_LABELS[key] ?? key} +${v.toFixed(1)}`)
                .join(", ");
              const unserved = s.unserved_twh ?? 0;
              return (
                <tr key={s.year} className="border-t border-slate-100">
                  <td className="py-1 pr-3">{s.year}</td>
                  <td className="py-1 pr-3">${s.carbon_price.toFixed(0)}</td>
                  <td className="py-1 pr-3">${s.system_lcoe.toFixed(1)}</td>
                  <td className="py-1 pr-3">{s.annual_emissions_mtco2.toFixed(0)}</td>
                  <td className="py-1 pr-3">{(s.emission_intensity * 1000).toFixed(0)}</td>
                  <td className="py-1 pr-3">{(s.import_dependency * 100).toFixed(0)}%</td>
                  <td className={`py-1 pr-3 ${unserved > 0.05 ? "text-rose-600" : "text-slate-400"}`}>
                    {unserved > 0.05 ? `${unserved.toFixed(1)} TWh` : "—"}
                  </td>
                  <td className="py-1 text-emerald-600" title={addedLabel}>
                    {addedTotal > 0.05 ? `+${addedTotal.toFixed(0)}` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
