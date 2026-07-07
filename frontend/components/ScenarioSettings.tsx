"use client";

import type { DispatchMode, EnsembleConfig, EnsembleMethod } from "@/lib/api";
import { InfoTip } from "@/components/InfoTip";

const WEATHER_YEAR_OPTIONS = [2020, 2021, 2022, 2023, 2024];

/**
 * Scenario-level modelling assumptions. These used to live under the left rail's
 * "Advanced" disclosure; they now live in the Parameters tab alongside the rest
 * of the country profile editing so the rail stays limited to the simple controls.
 */
export function ScenarioSettings({
  carbonPrice,
  rpsTarget,
  rpsPenalty,
  subsidyItc,
  subsidyPtc,
  fuelImportTariff,
  evPenetration,
  dispatchMode,
  weatherYears,
  ensemble,
  useCustomParameters,
  onCarbonPriceChange,
  onRpsTargetChange,
  onRpsPenaltyChange,
  onSubsidyItcChange,
  onSubsidyPtcChange,
  onFuelImportTariffChange,
  onEvPenetrationChange,
  onDispatchModeChange,
  onWeatherYearsChange,
  onEnsembleChange,
  onUseCustomParametersChange,
}: {
  carbonPrice: number;
  /** Renewable-share target, 0–1 (0 = off). */
  rpsTarget: number;
  /** Shortfall (REC) penalty, $/MWh. */
  rpsPenalty: number;
  /** Investment tax credit, 0–1 (fraction of capex) on clean generators. */
  subsidyItc: number;
  /** Production tax credit, $/MWh on clean generators. */
  subsidyPtc: number;
  /** Fuel-import tariff, fractional surcharge on imported fuel cost (0 = off). */
  fuelImportTariff: number;
  evPenetration: number;
  dispatchMode: DispatchMode;
  weatherYears: number[];
  ensemble: EnsembleConfig;
  useCustomParameters: boolean;
  onCarbonPriceChange: (value: number) => void;
  onRpsTargetChange: (value: number) => void;
  onRpsPenaltyChange: (value: number) => void;
  onSubsidyItcChange: (value: number) => void;
  onSubsidyPtcChange: (value: number) => void;
  onFuelImportTariffChange: (value: number) => void;
  onEvPenetrationChange: (value: number) => void;
  onDispatchModeChange: (value: DispatchMode) => void;
  onWeatherYearsChange: (value: number[]) => void;
  onEnsembleChange: (value: EnsembleConfig) => void;
  onUseCustomParametersChange: (value: boolean) => void;
}) {
  function updateEnsemble(next: Partial<EnsembleConfig>) {
    onEnsembleChange({ ...ensemble, ...next });
  }

  function toggleWeatherYear(year: number) {
    if (weatherYears.includes(year)) {
      onWeatherYearsChange(weatherYears.filter((value) => value !== year));
    } else {
      onWeatherYearsChange([...weatherYears, year].sort());
    }
  }

  return (
    <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-800">Scenario Settings</h3>

      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <span className="flex items-center gap-1.5">
            Carbon Price
            <InfoTip text="$/tonne CO2 added to fossil generators' running cost — it can reorder the merit stack." />
          </span>
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

      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <span className="flex items-center gap-1.5">
            Renewable Target
            <InfoTip text="Renewable Portfolio Standard: minimum share of generation from solar + wind. A shortfall (REC / alternative-compliance) penalty is added to system cost per point short." />
          </span>
          <span>{rpsTarget > 0 ? `${Math.round(rpsTarget * 100)}%` : "off"}</span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={Math.round(rpsTarget * 100)}
          onChange={(event) => onRpsTargetChange(Number(event.target.value) / 100)}
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
        />
        {rpsTarget > 0 ? (
          <label className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
            Shortfall penalty ($/MWh)
            <input
              type="number"
              min={0}
              value={rpsPenalty}
              onChange={(event) => onRpsPenaltyChange(Math.max(0, Number(event.target.value)))}
              className="w-20 rounded-lg border border-slate-200 bg-white px-2 py-1 text-right text-sm tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
            />
          </label>
        ) : null}
      </div>

      <div className="space-y-2 rounded-2xl border border-slate-200 bg-slate-50 p-3">
        <div className="flex items-center gap-1.5 text-sm font-medium text-slate-800">
          <span>Clean-Energy Subsidy</span>
          <InfoTip text="Support for solar + wind + nuclear: an investment tax credit (% of capex) and/or a production tax credit ($/MWh). The mirror image of the carbon price." />
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-between text-[11px] text-slate-500">
            <label>Investment credit (% of capex)</label>
            <span>{Math.round(subsidyItc * 100)}%</span>
          </div>
          <input
            type="range"
            min={0}
            max={50}
            step={5}
            value={Math.round(subsidyItc * 100)}
            onChange={(event) => onSubsidyItcChange(Number(event.target.value) / 100)}
            className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
          />
        </div>
        <label className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
          Production credit ($/MWh)
          <input
            type="number"
            min={0}
            value={subsidyPtc}
            onChange={(event) => onSubsidyPtcChange(Math.max(0, Number(event.target.value)))}
            className="w-20 rounded-lg border border-slate-200 bg-white px-2 py-1 text-right text-sm tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
          />
        </label>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <span className="flex items-center gap-1.5">
            Fuel Import Tariff
            <InfoTip text="Energy-security lever: a surcharge on the delivered price of imported fuel (gas, coal). It raises those generators' running cost — reordering the merit stack and the LCOE — pushing the mix toward domestic/clean supply." />
          </span>
          <span>{fuelImportTariff > 0 ? `+${Math.round(fuelImportTariff * 100)}%` : "off"}</span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={Math.round(fuelImportTariff * 100)}
          onChange={(event) => onFuelImportTariffChange(Number(event.target.value) / 100)}
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
        />
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-1.5 text-sm font-medium text-slate-800">
          <span>Dispatch Profile</span>
          <InfoTip text="Parametric uses smooth analytical curves; Data replays actual historical weather-year hourly profiles." />
        </div>
        <div className="grid grid-cols-2 gap-2 rounded-2xl border border-slate-200 bg-slate-50 p-1">
          {(["parametric", "data"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={dispatchMode === mode}
              onClick={() => onDispatchModeChange(mode)}
              className={[
                "rounded-xl px-3 py-2 text-sm font-medium transition",
                dispatchMode === mode
                  ? "bg-navy text-white shadow-sm"
                  : "text-slate-500 hover:bg-white hover:text-slate-800",
              ].join(" ")}
            >
              {mode === "parametric" ? "Parametric" : "Data"}
            </button>
          ))}
        </div>
      </div>

      {dispatchMode === "data" ? (
        <div className="space-y-2">
          <div className="text-sm font-medium text-slate-800">Weather Years</div>
          <div className="grid grid-cols-3 gap-2">
            {WEATHER_YEAR_OPTIONS.map((year) => (
              <label
                key={year}
                className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700"
              >
                <span>{year}</span>
                <input
                  type="checkbox"
                  checked={weatherYears.includes(year)}
                  onChange={() => toggleWeatherYear(year)}
                  className="h-4 w-4 rounded border-slate-300"
                />
              </label>
            ))}
          </div>
        </div>
      ) : null}

      <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-3">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <span className="flex items-center gap-1.5">
            Ensemble
            <InfoTip text="Runs several jointly-sampled weather years and reports a band + resource-adequacy (LOLE/EUE). Block-bootstrap resamples contiguous ~2-week blocks, preserving multi-day droughts — the correct sampler for adequacy; Jitter only perturbs one base year." />
          </span>
          <span>{ensemble.n_samples} profiles</span>
        </div>
        <select
          value={ensemble.method}
          onChange={(event) => updateEnsemble({ method: event.target.value as EnsembleMethod })}
          className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-slate-400"
        >
          <option value="single">Single</option>
          <option value="jitter">Jitter</option>
          <option value="multiyear">Multi-year</option>
          <option value="block_bootstrap">Block bootstrap (adequacy)</option>
        </select>
        {ensemble.method === "block_bootstrap" ? (
          <label className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
            <span className="flex items-center gap-1.5">
              Block length (days)
              <InfoTip text="Resampled block length. Must exceed the synoptic weather timescale (~3-7 days) or multi-day droughts get chopped at block seams and LOLE is under-stated. Two weeks is a safe default." />
            </span>
            <input
              type="number"
              min={1}
              max={60}
              value={ensemble.block_days ?? 14}
              onChange={(event) => updateEnsemble({ block_days: Math.max(1, Number(event.target.value)) })}
              className="w-20 rounded-lg border border-slate-200 bg-white px-2 py-1 text-right text-sm tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
            />
          </label>
        ) : null}
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-xs font-medium text-slate-600">
            Samples
            <input
              type="number"
              min={1}
              max={50}
              value={ensemble.n_samples}
              onChange={(event) => updateEnsemble({ n_samples: Number(event.target.value) })}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-slate-400"
            />
          </label>
          <label className="space-y-1 text-xs font-medium text-slate-600">
            Seed
            <input
              type="number"
              value={ensemble.seed}
              onChange={(event) => updateEnsemble({ seed: Number(event.target.value) })}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-slate-400"
            />
          </label>
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs font-medium text-slate-600">
            <label>Jitter Range</label>
            <span>{Math.round(ensemble.sigma * 100)}%</span>
          </div>
          <input
            type="range"
            min={0}
            max={20}
            step={1}
            value={Math.round(ensemble.sigma * 100)}
            onChange={(event) => updateEnsemble({ sigma: Number(event.target.value) / 100 })}
            className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
          />
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <span className="flex items-center gap-1.5">
            EV Penetration
            <InfoTip text="Share of the vehicle fleet electrified — shifts demand shape and adds smart-charging flexibility." />
          </span>
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
  );
}
