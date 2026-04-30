"use client";

import type { CountrySummary } from "@/lib/api";

export function ControlPanel({
  countries,
  country,
  carbonPrice,
  evPenetration,
  useCustomParameters,
  onCountryChange,
  onCarbonPriceChange,
  onEvPenetrationChange,
  onUseCustomParametersChange,
}: {
  countries: CountrySummary[];
  country: string;
  carbonPrice: number;
  evPenetration: number;
  useCustomParameters: boolean;
  onCountryChange: (country: string) => void;
  onCarbonPriceChange: (value: number) => void;
  onEvPenetrationChange: (value: number) => void;
  onUseCustomParametersChange: (value: boolean) => void;
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
          <label>Carbon Price</label>
          <span>${carbonPrice}/tCO2</span>
        </div>
        <input
          type="range"
          min={0}
          max={200}
          step={5}
          value={carbonPrice}
          onChange={(event) => onCarbonPriceChange(Number(event.target.value))}
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
        />
      </div>

      <details className="rounded-2xl border border-slate-200 bg-white p-4">
        <summary className="cursor-pointer text-sm font-medium text-slate-800">Advanced</summary>
        <div className="mt-4 space-y-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm font-medium text-slate-800">
              <label>EV Penetration</label>
              <span>{Math.round(evPenetration * 100)}%</span>
            </div>
            <input
              type="range"
              min={0}
              max={50}
              step={1}
              value={Math.round(evPenetration * 100)}
              onChange={(event) => onEvPenetrationChange(Number(event.target.value) / 100)}
              className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
            />
          </div>
          <label className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
            <span>Use Custom Parameters</span>
            <input
              type="checkbox"
              checked={useCustomParameters}
              onChange={(event) => onUseCustomParametersChange(event.target.checked)}
              className="h-4 w-4 rounded border-slate-300"
            />
          </label>
        </div>
      </details>
    </div>
  );
}
