"use client";

import type { CountrySummary } from "@/lib/api";
import { CountrySelector } from "@/components/CountrySelector";
import { InfoTip } from "@/components/InfoTip";

/** User-set storage rated power (GW) per tier. Duration is set in the Parameters ESS section. */
export interface StorageInput {
  shortPowerGw: number;
  phsPowerGw: number;
  longPowerGw: number;
}

export function ControlPanel({
  countries,
  country,
  storage,
  storageExpandable,
  addedStorageGw,
  addedStorageLongGw,
  annualDemandTwh,
  onCountryChange,
  onStorageChange,
  onStorageExpandableToggle,
  onAnnualDemandChange,
}: {
  countries: CountrySummary[];
  country: string;
  storage: StorageInput;
  storageExpandable: boolean;
  /** GW of short-duration storage power the solver added on the last run. */
  addedStorageGw?: number;
  /** GW of long-duration storage power the solver added on the last run. */
  addedStorageLongGw?: number;
  annualDemandTwh: number;
  onCountryChange: (country: string) => void;
  onStorageChange: (value: StorageInput) => void;
  onStorageExpandableToggle: (value: boolean) => void;
  onAnnualDemandChange: (value: number) => void;
}) {
  // Demand-slider bounds anchored to the selected country's real annual demand, so the range is
  // meaningful for a 2 TWh island grid and a 9000 TWh giant alike (a fixed 50–1200 TWh was not).
  const summary = countries.find((item) => item.code === country);
  const seedTwh = summary ? (summary.annual_demand_twh ?? summary.annual_generation_twh) : annualDemandTwh;
  const demandMin = Math.max(1, Math.round(seedTwh * 0.25));
  const demandMax = Math.max(10, Math.round(seedTwh * 2.5));
  const demandStep = Math.max(1, Math.round(seedTwh / 100));
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <label className="text-sm font-medium text-slate-800">Country</label>
        <CountrySelector countries={countries} value={country} onChange={onCountryChange} />
        <p className="text-[10px] text-slate-400">
          Demand &amp; installed capacity seeded from Ember Yearly Electricity Data
          {summary?.data_year ? ` (${summary.data_year})` : ""}
        </p>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <span className="flex items-center gap-1.5">
            Annual Demand
            <InfoTip text="Total electricity demand for the year (TWh) that the system must serve." />
          </span>
          <span>{annualDemandTwh.toFixed(0)} TWh</span>
        </div>
        <input
          type="range"
          min={demandMin}
          max={demandMax}
          step={demandStep}
          value={annualDemandTwh}
          onChange={(event) => onAnnualDemandChange(Number(event.target.value))}
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
        />
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <span className="flex items-center gap-1.5">
            Storage
            <InfoTip text="Rated power (GW) of battery (short), pumped-hydro (PHS) and seasonal (long) storage. Duration (hours) is set in Parameters → ESS; energy = power x duration." />
          </span>
          <span className="text-[10px] text-slate-400">GW</span>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {([
            ["Short power", "shortPowerGw", addedStorageGw],
            ["PHS power", "phsPowerGw", undefined],
            ["Long power", "longPowerGw", addedStorageLongGw],
          ] as const).map(([label, key, added]) => (
            <label key={key} className="flex flex-col gap-1 text-[10px] text-slate-400">
              <span className="flex items-center gap-1">
                {label} (GW)
                {added && added > 0 ? (
                  <span className="font-semibold text-emerald-600">+{added.toFixed(0)}</span>
                ) : null}
              </span>
              <input
                type="number"
                min={0}
                value={storage[key]}
                onChange={(event) =>
                  onStorageChange({ ...storage, [key]: Math.max(0, Number(event.target.value)) })
                }
                className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
              />
            </label>
          ))}
        </div>
        <label className="flex items-center justify-between gap-2 text-[10px] text-slate-500">
          <span>Expandable to meet 100% load</span>
          <input
            type="checkbox"
            checked={storageExpandable}
            onChange={(event) => onStorageExpandableToggle(event.target.checked)}
            className="h-3.5 w-3.5 rounded border-slate-300"
          />
        </label>
        <p className="text-[10px] text-slate-400">
          Endogenous: charges from surplus, discharges to shortfall. Duration set in Parameters → ESS.
        </p>
      </div>
    </div>
  );
}
