"use client";

import dynamic from "next/dynamic";

import type { ChronologicalPayload } from "@/lib/api";
import { GENERATOR_COLORS, GENERATOR_LABELS } from "@/lib/constants";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const PLOT_CONFIG: Partial<Plotly.Config> = {
  responsive: true,
  displayModeBar: false,
  scrollZoom: false,
};

/**
 * Chronological 8760-hour generation mix (time series, not the sorted duration curve).
 * Stacked generation by source with the demand line overlaid; a range slider lets the
 * user zoom from the full year down to a single week to see the day/night cycle.
 */
export function HourlyMixChart({
  chronological,
  loading,
}: {
  chronological: ChronologicalPayload | null;
  loading: boolean;
}) {
  if (!chronological) {
    return (
      <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.35)]">
        <h3 className="text-sm font-semibold text-slate-800">Hourly generation mix (8760 h)</h3>
        <div className="flex h-[380px] items-center justify-center text-sm text-slate-400">
          {loading ? "Building hourly dispatch…" : "Run analysis to see the hourly mix."}
        </div>
      </div>
    );
  }

  const { hours, series, resource_order } = chronological;
  const dayOfYear = hours.map((h) => h / 24);
  const stackKeys = resource_order.filter((key) => series[key]);

  const hasStorage = Array.isArray(series.storage);
  const traces: Plotly.Data[] = [
    ...stackKeys.map((key) => ({
      type: "scatter" as const,
      mode: "lines" as const,
      name: GENERATOR_LABELS[key] ?? key,
      x: dayOfYear,
      y: series[key],
      stackgroup: "mix",
      fillcolor: `${GENERATOR_COLORS[key] ?? "#64748b"}cc`,
      line: { width: 0, color: GENERATOR_COLORS[key] ?? "#64748b" },
      hovertemplate: `${GENERATOR_LABELS[key] ?? key}: %{y:.1f} GW<extra></extra>`,
    })),
    // Storage discharge stacks on top of generation (fills toward demand).
    ...(hasStorage
      ? [
          {
            type: "scatter" as const,
            mode: "lines" as const,
            name: "Storage (discharge)",
            x: dayOfYear,
            y: series.storage.map((v) => Math.max(v, 0)),
            stackgroup: "mix",
            fillcolor: "#ec4899cc",
            line: { width: 0, color: "#ec4899" },
            hovertemplate: `Storage discharge: %{y:.1f} GW<extra></extra>`,
          },
        ]
      : []),
    // Storage charge drawn below zero (absorbing surplus), outside the stack.
    ...(hasStorage
      ? [
          {
            type: "scatter" as const,
            mode: "lines" as const,
            name: "Storage (charge)",
            x: dayOfYear,
            y: series.storage.map((v) => Math.min(v, 0)),
            fill: "tozeroy" as const,
            fillcolor: "#ec489955",
            line: { width: 0, color: "#ec4899" },
            hovertemplate: `Storage charge: %{y:.1f} GW<extra></extra>`,
          },
        ]
      : []),
    {
      type: "scatter" as const,
      mode: "lines" as const,
      name: "Demand",
      x: dayOfYear,
      y: series.demand,
      line: { color: "#0f172a", width: 1 },
      hovertemplate: `Demand: %{y:.1f} GW<extra></extra>`,
    },
  ];

  const layout: Partial<Plotly.Layout> = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(248,250,252,1)",
    height: 400,
    margin: { l: 52, r: 16, b: 30, t: 8 },
    font: { family: "inherit", size: 11, color: "#64748b" },
    legend: { orientation: "h", x: 0, y: 1.08, font: { size: 10 } },
    hovermode: "x unified",
    xaxis: {
      title: { text: "Day of year", standoff: 8 },
      range: [0, 365],
      rangeslider: { visible: true, thickness: 0.08 },
      gridcolor: "#e2e8f0",
    },
    yaxis: { title: { text: "GW", standoff: 8 }, gridcolor: "#e2e8f0", zeroline: true, zerolinecolor: "#cbd5e1" },
  };

  return (
    <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.35)]">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-slate-800">Hourly generation mix (8760 h)</h3>
        <span className="text-[10px] text-slate-400">drag the slider to zoom to a week</span>
      </div>
      <Plot data={traces} layout={layout} config={PLOT_CONFIG} style={{ width: "100%", height: "400px" }} useResizeHandler />
    </div>
  );
}
