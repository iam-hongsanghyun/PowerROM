"use client";

import type { CurvePoint } from "@/lib/api";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function EmissionIntensityChart({
  data,
  selectedVreShare,
}: {
  data: CurvePoint[];
  selectedVreShare: number;
}) {
  const chartData = data.map((point) => ({
    ...point,
    emission_gco2_kwh: point.emission_intensity * 1000,
    upper: point.emission_intensity * 1100,
    lower: point.emission_intensity * 900,
  }));

  return (
    <div className="h-80 rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)]">
      <div className="mb-4">
        <h3 className="text-base font-semibold text-slate-900">Emission Intensity</h3>
        <p className="text-sm text-slate-500">Default uncertainty band shown at ±10%.</p>
      </div>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={chartData}>
          <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
          <XAxis dataKey="vre_share" tickFormatter={(value) => `${Math.round(value * 100)}%`} />
          <YAxis stroke="#64748b" unit=" g/kWh" />
          <Tooltip
            formatter={(value) =>
              typeof value === "number" ? `${value.toFixed(0)} gCO2/kWh` : String(value ?? "")
            }
          />
          <ReferenceLine x={selectedVreShare} stroke="#0f172a" strokeDasharray="6 6" />
          <Area type="monotone" dataKey="upper" stroke="none" fill="#dbeafe" />
          <Area type="monotone" dataKey="lower" stroke="none" fill="#ffffff" />
          <Line
            type="monotone"
            dataKey="emission_gco2_kwh"
            stroke="#1d4ed8"
            strokeWidth={3}
            dot={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
