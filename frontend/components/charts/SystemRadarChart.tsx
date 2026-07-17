"use client";

import { useState } from "react";

import type { RadarAxis, RadarPayload } from "@/lib/api";

// Hand-rolled SVG radar (no chart library, consistent with the other charts).
// Geometry: six axes at 60° steps starting from 12 o'clock, score 0 at the
// centre and 100 at the outer ring. Wider than tall so the side labels
// ("Independence", "Reliability") fit inside the viewBox without clipping.
const WIDTH = 500;
const HEIGHT = 380;
const CX = WIDTH / 2;
const CY = HEIGHT / 2;
const R = 132;
const RINGS = [25, 50, 75, 100];

// Series colors: scenario = PLANiT brand blue; baseline = neutral slate — a reference,
// not a competing series, so it deliberately reads gray and is dashed (identity is
// carried by the legend + dash pattern, not color alone).
const SCENARIO_COLOR = "#0174BE";
const BASELINE_COLOR = "#64748b";

const PILLAR_LABELS: Record<string, string> = {
  security: "Security",
  equity: "Equity",
  sustainability: "Sustainability",
};
// Which axes fold into each pillar (mirrors backend.core.radar.PILLARS) — tooltip copy only.
const PILLAR_AXES: Record<string, string> = {
  security: "Reliability · Resilience · Independence",
  equity: "Affordability · Price stability",
  sustainability: "Climate",
};

function vertex(index: number, count: number, score: number): [number, number] {
  const angle = -Math.PI / 2 + (2 * Math.PI * index) / count;
  const r = (R * Math.max(0, Math.min(100, score))) / 100;
  return [CX + r * Math.cos(angle), CY + r * Math.sin(angle)];
}

function polygonPoints(scores: number[]): string {
  return scores
    .map((score, i) => vertex(i, scores.length, score).map((v) => v.toFixed(1)).join(","))
    .join(" ");
}

/** Label anchor just outside the outer ring, aligned away from the centre. */
function labelPlacement(
  index: number,
  count: number,
): { x: number; y: number; anchor: "start" | "middle" | "end" } {
  const angle = -Math.PI / 2 + (2 * Math.PI * index) / count;
  const x = CX + (R + 16) * Math.cos(angle);
  const y = CY + (R + 16) * Math.sin(angle);
  const cos = Math.cos(angle);
  const anchor = cos > 0.35 ? "start" : cos < -0.35 ? "end" : "middle";
  return { x, y, anchor };
}

function AxisDetail({ axis, baseline }: { axis: RadarAxis; baseline?: RadarAxis }) {
  const delta = baseline ? axis.score - baseline.score : null;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-semibold text-slate-800">{axis.label}</span>
        <span className="text-lg font-semibold tabular-nums text-slate-900">{axis.score.toFixed(0)}</span>
        {delta != null && Math.abs(delta) >= 0.05 ? (
          <span
            className={`text-xs font-medium tabular-nums ${delta > 0 ? "text-emerald-600" : "text-rose-600"}`}
          >
            {delta > 0 ? "+" : ""}
            {delta.toFixed(1)} vs baseline
          </span>
        ) : null}
      </div>
      <div className="text-xs text-slate-600">
        {axis.value.toLocaleString(undefined, { maximumFractionDigits: 2 })} {axis.unit}
        {baseline
          ? ` · baseline ${baseline.value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${baseline.unit}`
          : ""}
      </div>
      <p className="text-[11px] leading-4 text-slate-400">{axis.detail}</p>
    </div>
  );
}

/**
 * System Radar: the scenario's six trilemma axes drawn over the country's real-mix
 * baseline. The gap between the two polygons is the policy story; every vertex
 * decomposes to the sourced physical number behind it on hover.
 */
export function SystemRadarChart({ radar }: { radar: RadarPayload }) {
  const [hovered, setHovered] = useState<number | null>(null);

  const axes = radar.axes;
  const baselineAxes = radar.baseline?.axes;
  const baselineByKey = new Map((baselineAxes ?? []).map((axis) => [axis.key, axis]));
  const scenarioScores = axes.map((axis) => axis.score);
  const baselineScores = baselineAxes ? axes.map((axis) => baselineByKey.get(axis.key)?.score ?? 0) : null;
  const hoveredAxis = hovered != null ? axes[hovered] : null;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-800">System Radar</h3>
        <div className="flex items-center gap-4 text-xs text-slate-500">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-5 rounded" style={{ backgroundColor: SCENARIO_COLOR }} />
            Scenario
          </span>
          {baselineScores ? (
            <span className="flex items-center gap-1.5">
              <svg width="20" height="2" aria-hidden>
                <line x1="0" y1="1" x2="20" y2="1" stroke={BASELINE_COLOR} strokeWidth="2" strokeDasharray="4 3" />
              </svg>
              Country baseline
            </span>
          ) : null}
        </div>
      </div>

      <div className="mt-2 flex flex-col gap-4 lg:flex-row lg:items-center">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="mx-auto w-full max-w-[480px] flex-shrink-0"
          role="img"
          aria-label="Radar of the six system scores for this scenario versus the country baseline"
          onMouseLeave={() => setHovered(null)}
        >
          {/* grid rings + spokes (recessive) */}
          {RINGS.map((ring) => (
            <polygon
              key={ring}
              points={polygonPoints(axes.map(() => ring))}
              fill="none"
              stroke="#e2e8f0"
              strokeWidth={ring === 100 ? 1.5 : 1}
            />
          ))}
          {axes.map((axis, i) => {
            const [x, y] = vertex(i, axes.length, 100);
            return <line key={axis.key} x1={CX} y1={CY} x2={x} y2={y} stroke="#e2e8f0" strokeWidth={1} />;
          })}
          <text x={CX + 4} y={CY - (R * 75) / 100 + 10} className="fill-slate-300 text-[9px]">
            75
          </text>

          {/* baseline polygon (reference: dashed, unfilled) */}
          {baselineScores ? (
            <polygon
              points={polygonPoints(baselineScores)}
              fill={BASELINE_COLOR}
              fillOpacity={0.06}
              stroke={BASELINE_COLOR}
              strokeWidth={2}
              strokeDasharray="5 4"
              strokeLinejoin="round"
            />
          ) : null}

          {/* scenario polygon */}
          <polygon
            points={polygonPoints(scenarioScores)}
            fill={SCENARIO_COLOR}
            fillOpacity={0.16}
            stroke={SCENARIO_COLOR}
            strokeWidth={2}
            strokeLinejoin="round"
          />

          {/* vertices, labels, and oversized hover targets */}
          {axes.map((axis, i) => {
            const [x, y] = vertex(i, axes.length, axis.score);
            const { x: lx, y: ly, anchor } = labelPlacement(i, axes.length);
            const active = hovered === i;
            return (
              <g key={axis.key}>
                <circle
                  cx={x}
                  cy={y}
                  r={active ? 5 : 3.5}
                  fill={SCENARIO_COLOR}
                  stroke="#ffffff"
                  strokeWidth={2}
                />
                <text
                  x={lx}
                  y={ly - 2}
                  textAnchor={anchor}
                  className={`text-[11px] font-medium ${active ? "fill-slate-900" : "fill-slate-500"}`}
                >
                  {axis.label}
                </text>
                <text
                  x={lx}
                  y={ly + 10}
                  textAnchor={anchor}
                  className="fill-slate-900 text-[11px] font-semibold tabular-nums"
                >
                  {axis.score.toFixed(0)}
                </text>
                {/* hover hit target: the whole sector wedge is too fiddly; a generous circle on
                    the outer vertex position keeps the target ≥28px regardless of the score */}
                <circle
                  cx={vertex(i, axes.length, Math.max(axis.score, 55))[0]}
                  cy={vertex(i, axes.length, Math.max(axis.score, 55))[1]}
                  r={26}
                  fill="transparent"
                  onMouseEnter={() => setHovered(i)}
                />
              </g>
            );
          })}
        </svg>

        <div className="min-w-0 flex-1 space-y-3">
          {/* trilemma pillars (WEC-comparable headline) */}
          <div className="grid grid-cols-3 gap-2">
            {Object.entries(PILLAR_LABELS).map(([key, label]) =>
              radar.pillars[key] != null ? (
                <div
                  key={key}
                  title={PILLAR_AXES[key]}
                  className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2"
                >
                  <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
                  <div className="text-lg font-semibold tabular-nums text-slate-900">
                    {radar.pillars[key].toFixed(0)}
                  </div>
                </div>
              ) : null,
            )}
          </div>

          {/* hovered-axis decomposition (the score is a view of the model, never a new opinion) */}
          <div className="min-h-[84px] rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
            {hoveredAxis ? (
              <AxisDetail axis={hoveredAxis} baseline={baselineByKey.get(hoveredAxis.key)} />
            ) : (
              <p className="text-xs leading-5 text-slate-400">
                Hover an axis to see the physical number behind its score and how it compares to the
                country&apos;s real-mix baseline.
              </p>
            )}
          </div>

          {/* axis table (accessibility: identity and values never color-alone) */}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-[10px] uppercase tracking-wide text-slate-400">
                  <th className="py-1 pr-2 font-medium">Axis</th>
                  <th className="py-1 pr-2 text-right font-medium">Scenario</th>
                  {baselineScores ? <th className="py-1 text-right font-medium">Baseline</th> : null}
                </tr>
              </thead>
              <tbody className="text-slate-600">
                {axes.map((axis, i) => (
                  <tr
                    key={axis.key}
                    className={`cursor-default border-t border-slate-100 ${hovered === i ? "bg-slate-50" : ""}`}
                    onMouseEnter={() => setHovered(i)}
                    onMouseLeave={() => setHovered(null)}
                  >
                    <td className="py-1 pr-2">{axis.label}</td>
                    <td className="py-1 pr-2 text-right font-semibold tabular-nums text-slate-900">
                      {axis.score.toFixed(0)}
                    </td>
                    {baselineScores ? (
                      <td className="py-1 text-right tabular-nums">
                        {(baselineByKey.get(axis.key)?.score ?? 0).toFixed(0)}
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <p className="mt-3 text-[11px] leading-4 text-slate-400">
        {radar.method}
        {radar.baseline?.note ? ` ${radar.baseline.note}` : ""}
      </p>
    </div>
  );
}
