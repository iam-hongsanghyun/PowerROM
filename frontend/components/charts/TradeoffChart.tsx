"use client";

import type { CurvePoint } from "@/lib/api";
import {
  CartesianGrid,
  ReferenceDot,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function TradeoffChart({
  data,
  currentPoint,
}: {
  data: CurvePoint[];
  currentPoint: { lcoe: number; emission: number };
}) {
  const chartData = data.map((point) => ({
    lcoe: point.system_lcoe,
    emission: point.emission_intensity * 1000,
  }));

  return (
    <div className="h-80 rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)]">
      <div className="mb-4">
        <h3 className="text-base font-semibold text-slate-900">Trade-off Frontier</h3>
        <p className="text-sm text-slate-500">LCOE versus emissions across the VRE range.</p>
      </div>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart>
          <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
          <XAxis dataKey="lcoe" type="number" stroke="#64748b" unit=" $" />
          <YAxis dataKey="emission" type="number" stroke="#64748b" unit=" g/kWh" />
          <Tooltip
            formatter={(value) =>
              typeof value === "number" ? value.toFixed(1) : String(value ?? "")
            }
          />
          <Scatter data={chartData} fill="#1d4ed8" line shape="circle" />
          <ReferenceDot
            x={currentPoint.lcoe}
            y={currentPoint.emission * 1000}
            r={7}
            fill="#dc2626"
            stroke="#ffffff"
          />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
