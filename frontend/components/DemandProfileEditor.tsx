"use client";

import { useRef } from "react";

export interface DemandProfile {
  monthly: number[]; // 12 relative seasonal levels
  daily: number[]; // 24 relative hour-of-day levels
}

const MONTH_LABELS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

export const DEFAULT_DEMAND_PROFILE: DemandProfile = {
  monthly: [1.12, 1.06, 1.0, 0.94, 0.9, 0.9, 0.95, 0.96, 0.95, 1.0, 1.06, 1.12],
  daily: [
    0.82, 0.78, 0.75, 0.74, 0.76, 0.82, 0.9, 0.98, 1.03, 1.04, 1.03, 1.02,
    1.0, 1.0, 1.0, 1.03, 1.1, 1.22, 1.3, 1.26, 1.16, 1.04, 0.94, 0.86,
  ],
};

const clamp = (value: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, value));

/** Draggable SVG bar editor — click or drag across the bars to draw the shape. */
function BarEditor({
  values,
  labels,
  color,
  onChange,
}: {
  values: number[];
  labels: string[];
  color: string;
  onChange: (next: number[]) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const dragging = useRef(false);
  const W = 100;
  const H = 42;
  const MIN = 0.4;
  const MAX = 1.8;
  const n = values.length;
  const bw = W / n;
  const toY = (v: number) => H - ((clamp(v, MIN, MAX) - MIN) / (MAX - MIN)) * H;

  function setFromEvent(event: React.PointerEvent<SVGSVGElement>) {
    const rect = svgRef.current!.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * W;
    const y = ((event.clientY - rect.top) / rect.height) * H;
    const idx = clamp(Math.floor(x / bw), 0, n - 1);
    const value = clamp(MIN + ((H - y) / H) * (MAX - MIN), MIN, MAX);
    const next = [...values];
    next[idx] = Math.round(value * 100) / 100;
    onChange(next);
  }

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="h-24 w-full cursor-crosshair touch-none rounded-lg bg-slate-50"
      onPointerDown={(e) => {
        dragging.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        setFromEvent(e);
      }}
      onPointerMove={(e) => dragging.current && setFromEvent(e)}
      onPointerUp={() => (dragging.current = false)}
    >
      {/* baseline at 1.0 */}
      <line x1={0} x2={W} y1={toY(1)} y2={toY(1)} stroke="#cbd5e1" strokeWidth={0.3} strokeDasharray="1 1" />
      {values.map((v, i) => (
        <rect
          key={i}
          x={i * bw + 0.4}
          y={toY(v)}
          width={bw - 0.8}
          height={H - toY(v)}
          fill={color}
          opacity={0.85}
        />
      ))}
      {labels.map((label, i) => (
        <text key={i} x={i * bw + bw / 2} y={H - 1} fontSize={2.4} fill="#94a3b8" textAnchor="middle">
          {label}
        </text>
      ))}
    </svg>
  );
}

export function DemandProfileEditor({
  profile,
  onChange,
}: {
  profile: DemandProfile;
  onChange: (next: DemandProfile) => void;
}) {
  return (
    <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-800">Demand Profile</h3>
        <button
          type="button"
          onClick={() => onChange({ monthly: [...DEFAULT_DEMAND_PROFILE.monthly], daily: [...DEFAULT_DEMAND_PROFILE.daily] })}
          className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] text-slate-500 transition hover:bg-slate-50"
        >
          Reset
        </button>
      </div>

      <div className="space-y-1">
        <div className="text-[11px] font-medium text-slate-500">Monthly (seasonal peak &amp; bottom)</div>
        <BarEditor
          values={profile.monthly}
          labels={MONTH_LABELS}
          color="#0ea5e9"
          onChange={(monthly) => onChange({ ...profile, monthly })}
        />
      </div>

      <div className="space-y-1">
        <div className="text-[11px] font-medium text-slate-500">Daily (hour-of-day pattern)</div>
        <BarEditor
          values={profile.daily}
          labels={profile.daily.map((_, i) => (i % 3 === 0 ? String(i) : ""))}
          color="#8b5cf6"
          onChange={(daily) => onChange({ ...profile, daily })}
        />
      </div>

      <p className="text-[10px] text-slate-400">
        Drag across the bars to draw the shape. Values are relative to the annual average
        (dashed line = 1.0); the profile is normalised so annual energy is unchanged.
      </p>
    </div>
  );
}
