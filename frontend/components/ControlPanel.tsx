"use client";

import type { CountrySummary } from "@/lib/api";
import type { DispatchMode, EnsembleConfig, EnsembleMethod } from "@/lib/api";

const WEATHER_YEAR_OPTIONS = [2020, 2021, 2022, 2023, 2024];

export function ControlPanel({
  countries,
  country,
  carbonPrice,
  essCostUsdKwh,
  evPenetration,
  annualDemandTwh,
  dispatchMode,
  weatherYears,
  ensemble,
  useCustomParameters,
  onCountryChange,
  onCarbonPriceChange,
  onEssCostChange,
  onEvPenetrationChange,
  onAnnualDemandChange,
  onDispatchModeChange,
  onWeatherYearsChange,
  onEnsembleChange,
  onUseCustomParametersChange,
}: {
  countries: CountrySummary[];
  country: string;
  carbonPrice: number;
  essCostUsdKwh: number;
  evPenetration: number;
  annualDemandTwh: number;
  dispatchMode: DispatchMode;
  weatherYears: number[];
  ensemble: EnsembleConfig;
  useCustomParameters: boolean;
  onCountryChange: (country: string) => void;
  onCarbonPriceChange: (value: number) => void;
  onEssCostChange: (value: number) => void;
  onEvPenetrationChange: (value: number) => void;
  onAnnualDemandChange: (value: number) => void;
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

      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <label>Battery Cost (ESS)</label>
          <span>${essCostUsdKwh}/kWh</span>
        </div>
        <input
          type="range"
          min={50}
          max={600}
          step={10}
          value={essCostUsdKwh}
          onChange={(event) => onEssCostChange(Number(event.target.value))}
          className="h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
        />
        <p className="text-[10px] text-slate-400">
          Today ~$280 · 2030 target ~$120 · Higher → more expensive VRE
        </p>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm font-medium text-slate-800">
          <label>Annual Demand</label>
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

      <details className="rounded-2xl border border-slate-200 bg-white p-4">
        <summary className="cursor-pointer text-sm font-medium text-slate-800">Advanced</summary>
        <div className="mt-4 space-y-4">
          <div className="space-y-2">
            <div className="text-sm font-medium text-slate-800">Dispatch Profile</div>
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
                      ? "bg-slate-900 text-white shadow-sm"
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
              <label>Ensemble</label>
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
            </select>
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
