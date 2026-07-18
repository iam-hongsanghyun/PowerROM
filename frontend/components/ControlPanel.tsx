"use client";

import type { CountrySummary } from "@/lib/api";
import { CountrySelector } from "@/components/CountrySelector";
import { InfoTip } from "@/components/InfoTip";

export function ControlPanel({
  countries,
  country,
  annualDemandTwh,
  onCountryChange,
  onAnnualDemandChange,
}: {
  countries: CountrySummary[];
  country: string;
  annualDemandTwh: number;
  onCountryChange: (country: string) => void;
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
    </div>
  );
}
