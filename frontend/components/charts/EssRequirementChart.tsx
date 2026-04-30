"use client";

import type { CurvePoint } from "@/lib/api";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function EssRequirementChart({
  data,
  selectedVreShare,
}: {
  data: CurvePoint[];
  selectedVreShare: number;
}) {
  return (
    <div className="h-80 rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)]">
      <div className="mb-4">
        <h3 className="text-base font-semibold text-slate-900">ESS Requirement</h3>
        <p className="text-sm text-slate-500">Storage requirement in GWh across VRE shares.</p>
      </div>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
          <XAxis dataKey="vre_share" tickFormatter={(value) => `${Math.round(value * 100)}%`} />
          <YAxis stroke="#64748b" unit=" GWh" />
          <Tooltip
            formatter={(value) =>
              typeof value === "number" ? `${value.toFixed(0)} GWh` : String(value ?? "")
            }
          />
          <ReferenceLine x={selectedVreShare} stroke="#0f172a" strokeDasharray="6 6" />
          <Line type="monotone" dataKey="ess_gwh" stroke="#059669" strokeWidth={3} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
