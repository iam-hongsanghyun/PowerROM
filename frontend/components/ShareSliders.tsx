"use client";

import type { GeneratorKey, Shares } from "@/lib/api";

const GENERATORS: Array<{
  key: GeneratorKey;
  label: string;
  color: string;
}>= [
  { key: "solar", label: "Solar", color: "#f6c945" },
  { key: "wind_onshore", label: "Wind", color: "#3b82f6" },
  { key: "gas_ccgt", label: "Gas", color: "#f97316" },
  { key: "coal", label: "Coal", color: "#6b7280" },
  { key: "nuclear", label: "Nuclear", color: "#8b5cf6" },
  { key: "other", label: "Other", color: "#10b981" },
];

function rebalanceShares(shares: Shares, key: GeneratorKey, nextValue: number): Shares {
  const clampedTarget = Math.min(Math.max(nextValue, 0), 1);
  const remainingKeys = GENERATORS.map((item) => item.key).filter((item) => item !== key);
  const remainingCurrentTotal = remainingKeys.reduce((sum, item) => sum + shares[item], 0);
  const remainingTargetTotal = 1 - clampedTarget;

  const nextShares: Shares = { ...shares, [key]: clampedTarget };

  if (remainingCurrentTotal <= 0) {
    const equalShare = remainingTargetTotal / remainingKeys.length;
    remainingKeys.forEach((item) => {
      nextShares[item] = equalShare;
    });
  } else {
    remainingKeys.forEach((item) => {
      nextShares[item] = (shares[item] / remainingCurrentTotal) * remainingTargetTotal;
    });
  }

  const correction = 1 - Object.values(nextShares).reduce((sum, value) => sum + value, 0);
  nextShares.other = Math.max(nextShares.other + correction, 0);
  return nextShares;
}

export function ShareSliders({
  shares,
  onChange,
}: {
  shares: Shares;
  onChange: (shares: Shares) => void;
}) {
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
              onChange={(event) =>
                onChange(rebalanceShares(shares, generator.key, Number(event.target.value) / 100))
              }
              className="slider h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
              style={{
                background: `linear-gradient(90deg, ${generator.color} 0%, ${generator.color} ${value * 100}%, #e2e8f0 ${value * 100}%, #e2e8f0 100%)`,
              }}
            />
          </label>
        );
      })}
      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
        Total share: {(Object.values(shares).reduce((sum, value) => sum + value, 0) * 100).toFixed(1)}%
      </div>
    </div>
  );
}
