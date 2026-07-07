"use client";

import type { CountrySummary } from "@/lib/api";
import { InfoTip } from "@/components/InfoTip";

/** User-set storage rated power (GW) per tier. Duration is set in the Parameters ESS section. */
export interface StorageInput {
  shortPowerGw: number;
  longPowerGw: number;
}

export function ControlPanel({
  countries,
  country,
  storage,
  storageExpandable,
  addedStorageGw,
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
  annualDemandTwh: number;
  onCountryChange: (country: string) => void;
  onStorageChange: (value: StorageInput) => void;
  onStorageExpandableToggle: (value: boolean) => void;
  onAnnualDemandChange: (value: number) => void;
}) {
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <label className="text-sm font-medium text-slate-800">Country</label>
        <select
          value={country}
          onChange={(event) => onCountryChange(event.target.value)}
          className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
        >
          {countries.map((item) => (
            <option key={item.code} value={item.code}>
              {item.code} · {item.name}
            </option>
          ))}
        </select>
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
          min={50}
          max={1200}
          step={10}
          value={annualDemandTwh}
          onChange={(event) => onAnnualDemandChange(Number(event.target.value))}
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
        />
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <span className="flex items-center gap-1.5">
            Storage
            <InfoTip text="Rated power (GW) of short- and long-duration storage. Duration (hours) is set in Parameters → ESS; energy = power x duration." />
          </span>
          <span className="text-[10px] text-slate-400">GW</span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {([
            ["Short power", "shortPowerGw"],
            ["Long power", "longPowerGw"],
          ] as const).map(([label, key]) => (
            <label key={key} className="flex flex-col gap-1 text-[10px] text-slate-400">
              {label} (GW)
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
          <span className="flex items-center gap-1">
            Expandable to meet 100% load
            {addedStorageGw && addedStorageGw > 0 ? (
              <span className="font-semibold text-emerald-600">+{addedStorageGw.toFixed(0)} GW</span>
            ) : null}
          </span>
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
