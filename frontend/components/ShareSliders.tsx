"use client";

import type { GeneratorKey, Shares } from "@/lib/api";

const GENERATORS: Array<{
  key: GeneratorKey;
  label: string;
  color: string;
}> = [
  { key: "solar", label: "Solar", color: "#f6c945" },
  { key: "wind_onshore", label: "Wind", color: "#3b82f6" },
  { key: "gas_ccgt", label: "Gas", color: "#f97316" },
  { key: "coal", label: "Coal", color: "#6b7280" },
  { key: "nuclear", label: "Nuclear", color: "#8b5cf6" },
  { key: "other", label: "Other", color: "#10b981" },
];

export function ShareSliders({
  shares,
  onChange,
}: {
  shares: Shares;
  onChange: (shares: Shares) => void;
}) {
  const totalShare = Object.values(shares).reduce((sum, value) => sum + value, 0);
  const remainingShare = 1 - totalShare;

  return (
    <div className="space-y-5">
      {GENERATORS.map((generator) => {
        const value = shares[generator.key];
        return (
          <label key={generator.key} className="block space-y-2">
            <div className="flex items-center justify-between text-sm font-medium text-slate-800">
              <span className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: generator.color }}
                />
                {generator.label}
              </span>
              <span>{(value * 100).toFixed(1)}%</span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={Math.round(value * 100)}
              onChange={(event) => onChange({ ...shares, [generator.key]: Number(event.target.value) / 100 })}
              className="slider h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
              style={{
                background: `linear-gradient(90deg, ${generator.color} 0%, ${generator.color} ${value * 100}%, #e2e8f0 ${value * 100}%, #e2e8f0 100%)`,
              }}
            />
          </label>
        );
      })}
      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
        <div>Total share: {(totalShare * 100).toFixed(1)}%</div>
        <div>
          Remaining: {remainingShare >= 0 ? "+" : "-"}
          {(Math.abs(remainingShare) * 100).toFixed(1)}%
        </div>
        <div className="mt-1 text-slate-500">Calculation normalizes shares to 100% automatically.</div>
      </div>
    </div>
  );
}
