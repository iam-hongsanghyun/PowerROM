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
  const remainingPct = Math.round((1 - totalShare) * 100);

  function handleChange(key: GeneratorKey, rawPct: number) {
    // Sum of all generators except the one being changed
    const othersTotal = Object.entries(shares)
      .filter(([k]) => k !== key)
      .reduce((s, [, v]) => s + v, 0);
    // Hard cap: this generator can take at most what's left after others
    const maxPct = Math.max(0, Math.round((1 - othersTotal) * 100));
    const clampedPct = Math.min(rawPct, maxPct);
    onChange({ ...shares, [key]: clampedPct / 100 });
  }

  return (
    <div className="space-y-5">
      {GENERATORS.map((generator) => {
        const value = shares[generator.key];
        const valuePct = Math.round(value * 100);
        // Visual: always 0–100 range so position is intuitive.
        // Clamping happens in handleChange — dragging right past the limit
        // simply stops the value from increasing further.
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
              <span>{valuePct}%</span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={valuePct}
              onChange={(e) => handleChange(generator.key, Number(e.target.value))}
              className="slider h-2 w-full cursor-pointer appearance-none rounded-full bg-slate-200"
              style={{
                background: `linear-gradient(90deg, ${generator.color} 0%, ${generator.color} ${valuePct}%, #e2e8f0 ${valuePct}%, #e2e8f0 100%)`,
              }}
            />
          </label>
        );
      })}

      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
        <div className="flex items-center justify-between">
          <span>Total</span>
          <span className="font-semibold">{Math.round(totalShare * 100)}%</span>
        </div>
        <div className="mt-1 flex items-center justify-between">
          <span>Remaining (available to assign)</span>
          <span className={remainingPct > 0 ? "font-semibold text-emerald-600" : "font-semibold text-slate-400"}>
            {remainingPct}%
          </span>
        </div>
      </div>
    </div>
  );
}
