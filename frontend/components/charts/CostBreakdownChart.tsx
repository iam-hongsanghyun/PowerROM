"use client";

import { ChartViewport } from "./ChartViewport";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function CostBreakdownChart({
  lcoeByGenerator,
}: {
  lcoeByGenerator: Record<string, Record<string, number | string>>;
}) {
  const data = Object.entries(lcoeByGenerator).map(([key, value]) => ({
    generator: key,
    contribution: Number(value.share_weighted_cost ?? 0),
  }));
  const hasData = data.length > 0;

  return (
    <div className="h-80 rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)]">
      <div className="mb-4">
        <h3 className="text-base font-semibold text-slate-900">Cost Breakdown</h3>
        <p className="text-sm text-slate-500">Share-weighted generator contributions at the selected mix.</p>
      </div>
      {!hasData ? (
        <div className="flex h-56 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500">
          Waiting for calculation data
        </div>
      ) : (
        <ChartViewport>
          {({ width, height }) => (
            <BarChart width={width} height={height} data={data}>
              <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
              <XAxis dataKey="generator" />
              <YAxis stroke="#64748b" />
              <Tooltip
                formatter={(value) =>
                  typeof value === "number" ? `$${value.toFixed(1)}/MWh` : String(value ?? "")
                }
              />
              <Bar dataKey="contribution" fill="#0f766e" radius={[8, 8, 0, 0]} />
            </BarChart>
          )}
        </ChartViewport>
      )}
    </div>
  );
}
