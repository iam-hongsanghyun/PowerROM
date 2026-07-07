"use client";

import type { CurvePoint } from "@/lib/api";
import { ChartViewport } from "./ChartViewport";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function SystemLcoeChart({
  data,
  selectedVreShare,
}: {
  data: CurvePoint[];
  selectedVreShare: number;
}) {
  const hasData = data.length > 0;

  return (
    <div className="h-80 rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)]">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-slate-900">System LCOE Curve</h3>
          <p className="text-sm text-slate-500">Stacked cost components across VRE share.</p>
        </div>
      </div>
      {!hasData ? (
        <div className="flex h-56 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500">
          Waiting for calculation data
        </div>
      ) : (
        <ChartViewport>
          {({ width, height }) => (
            <AreaChart width={width} height={height} data={data}>
              <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
              <XAxis
                dataKey="vre_share"
                tickFormatter={(value) => `${Math.round(value * 100)}%`}
                stroke="#64748b"
              />
              <YAxis stroke="#64748b" unit=" $" />
              <Tooltip
                formatter={(value) =>
                  typeof value === "number" ? `$${value.toFixed(1)}/MWh` : String(value ?? "")
                }
              />
              <ReferenceLine x={selectedVreShare} stroke="#0f172a" strokeDasharray="6 6" />
              <Area type="monotone" dataKey="capex" stackId="1" stroke="#2563eb" fill="#93c5fd" />
              <Area type="monotone" dataKey="fuel" stackId="1" stroke="#ea580c" fill="#fdba74" />
              <Area type="monotone" dataKey="carbon" stackId="1" stroke="#dc2626" fill="#fca5a5" />
              <Area type="monotone" dataKey="ess" stackId="1" stroke="#059669" fill="#6ee7b7" />
            </AreaChart>
          )}
        </ChartViewport>
      )}
    </div>
  );
}
