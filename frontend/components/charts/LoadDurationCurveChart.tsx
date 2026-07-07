"use client";

import dynamic from "next/dynamic";

import type { DispatchSummary, LdcPayload, LdcSeriesBand } from "@/lib/api";
import { GENERATOR_COLORS, GENERATOR_LABELS } from "@/lib/constants";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const PLOT_CONFIG: Partial<Plotly.Config> = {
  responsive: true,
  displayModeBar: false,
  scrollZoom: false,
};

const LDC_DISPLAY_BINS = 120;

function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function binArray(values: number[], bins: number): number[] {
  if (values.length <= bins) return values;
  return Array.from({ length: bins }, (_, index) => {
    const start = Math.floor((index * values.length) / bins);
    const end = Math.floor(((index + 1) * values.length) / bins);
    return mean(values.slice(start, Math.max(start + 1, end)));
  });
}

function binBand(series: LdcSeriesBand, bins: number): LdcSeriesBand {
  return {
    p10: binArray(series.p10, bins),
    median: binArray(series.median, bins),
    p90: binArray(series.p90, bins),
  };
}

function binLdcPayload(ldc: LdcPayload, bins: number): LdcPayload {
  if (ldc.x_percent.length <= bins) return ldc;
  const series = Object.fromEntries(
    Object.entries(ldc.series).map(([key, value]) => [key, binBand(value, bins)]),
  );
  return {
    ...ldc,
    x_hours: binArray(ldc.x_hours, bins),
    x_percent: binArray(ldc.x_percent, bins),
    series,
  };
}

function bandTrace(
  x: number[],
  lower: number[],
  upper: number[],
  name: string,
  color: string,
): Plotly.Data[] {
  return [
    {
      type: "scatter" as const,
      mode: "lines" as const,
      name: `${name} p90`,
      x,
      y: upper,
      line: { color: "rgba(0,0,0,0)", width: 0 },
      hoverinfo: "skip",
      showlegend: false,
    },
    {
      type: "scatter" as const,
      mode: "lines" as const,
      name: `${name} p10-p90`,
      x,
      y: lower,
      fill: "tonexty" as const,
      fillcolor: color,
      line: { color: "rgba(0,0,0,0)", width: 0 },
      hoverinfo: "skip",
      showlegend: true,
    },
  ];
}

function scalarMedian(dispatch: DispatchSummary | null, key: string): number | null {
  return dispatch?.metrics.scalars[key]?.median ?? null;
}

interface Props {
  ldc: LdcPayload | null;
  dispatch: DispatchSummary | null;
  loading?: boolean;
}

export function LoadDurationCurveChart({ ldc, dispatch, loading = false }: Props) {
  if (!ldc) {
    return (
      <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.35)]">
        <div className="flex h-[420px] items-center justify-center text-sm text-slate-400">
          {loading ? "Building hourly dispatch…" : "Hourly dispatch will appear here."}
        </div>
      </div>
    );
  }

  const displayLdc = binLdcPayload(ldc, LDC_DISPLAY_BINS);
  const x = displayLdc.x_percent;
  const netLoad = displayLdc.series.net_load;
  const demand = displayLdc.series.demand;
  const servedLoad = displayLdc.series.served_load;
  const curtailed = displayLdc.series.curtailed_vre;
  const unserved = displayLdc.series.unserved;
  const stackKeys = displayLdc.resource_order.filter((key) => displayLdc.series[key]);

  const traces: Plotly.Data[] = [
    ...bandTrace(x, demand.p10, demand.p90, "Load band", "rgba(14,165,233,0.16)"),
    ...stackKeys.map((key) => ({
      type: "scatter" as const,
      mode: "lines" as const,
      name: GENERATOR_LABELS[key] ?? key,
      x,
      y: displayLdc.series[key]!.median,
      stackgroup: "dispatch",
      fillcolor: `${GENERATOR_COLORS[key] ?? "#64748b"}cc`,
      line: { color: GENERATOR_COLORS[key] ?? "#64748b", width: 0.6 },
      hovertemplate: `${GENERATOR_LABELS[key] ?? key}: %{y:.2f} GW<br>%{x:.1f}% of hours<extra></extra>`,
    })),
    {
      type: "scatter" as const,
      mode: "lines" as const,
      name: "Demand",
      x,
      y: demand.median,
      line: { color: "#0f172a", width: 1.8 },
      hovertemplate: "Demand: %{y:.2f} GW<br>%{x:.1f}% of hours<extra></extra>",
    },
    {
      type: "scatter" as const,
      mode: "lines" as const,
      name: "Served load",
      x,
      y: servedLoad.median,
      line: { color: "#94a3b8", width: 1.2, dash: "dot" as const },
      hovertemplate: "Served load: %{y:.2f} GW<br>%{x:.1f}% of hours<extra></extra>",
    },
    {
      type: "scatter" as const,
      mode: "lines" as const,
      name: "Net load after VRE",
      x,
      y: netLoad.median,
      line: { color: "#475569", width: 1.0, dash: "dash" as const },
      hovertemplate: "Net load after VRE: %{y:.2f} GW<br>%{x:.1f}% of hours<extra></extra>",
    },
    {
      type: "scatter" as const,
      mode: "lines" as const,
      name: "Curtailed VRE",
      x,
      y: curtailed.median,
      line: { color: "#ef4444", width: 1.3, dash: "dash" as const },
      hovertemplate: "Curtailed VRE: %{y:.2f} GW<br>%{x:.1f}% of hours<extra></extra>",
    },
  ];

  if (Math.max(...unserved.median) > 0.001) {
    traces.push({
      type: "scatter" as const,
      mode: "lines" as const,
      name: "Unserved",
      x,
      y: unserved.median,
      line: { color: "#be123c", width: 1.4, dash: "dashdot" as const },
      hovertemplate: "Unserved: %{y:.2f} GW<br>%{x:.1f}% of hours<extra></extra>",
    });
  }

  const curtailment = scalarMedian(dispatch, "curtailment_rate");
  const unservedTwh = scalarMedian(dispatch, "unserved_twh");
  const peakLoad = scalarMedian(dispatch, "peak_load_gw");

  const layout: Partial<Plotly.Layout> = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(248,250,252,1)",
    margin: { l: 62, r: 24, b: 136, t: 10 },
    font: { family: "inherit", size: 11, color: "#64748b" },
    dragmode: false,
    hovermode: "x unified",
    legend: {
      orientation: "h",
      x: 0,
      y: -0.26,
      bgcolor: "rgba(255,255,255,0)",
      font: { size: 10 },
    },
    xaxis: {
      title: { text: "Hours Sorted by Gross Load (%)" },
      gridcolor: "#e2e8f0",
      range: [0, 100],
      fixedrange: true,
    },
    yaxis: {
      title: { text: "Dispatch / Load (GW)" },
      gridcolor: "#e2e8f0",
      rangemode: "tozero",
      fixedrange: true,
    },
  };

  return (
    <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.35)]">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-800">Hourly Merit-Order Load Duration Curve</h3>
          <p className="text-xs text-slate-400">
            {dispatch
              ? `${dispatch.ensemble.n_samples} ${dispatch.ensemble.method} profile${dispatch.ensemble.n_samples === 1 ? "" : "s"} · ${dispatch.mode} mode`
              : "Dispatch ensemble"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-slate-600">
            Peak {peakLoad?.toFixed(1) ?? "--"} GW
          </span>
          <span className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-slate-600">
            Curtailment {curtailment !== null ? (curtailment * 100).toFixed(1) : "--"}%
          </span>
          <span className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-slate-600">
            Unserved {unservedTwh?.toFixed(1) ?? "--"} TWh
          </span>
        </div>
      </div>
      <div className="h-[420px]">
        <Plot
          data={traces}
          layout={layout}
          config={PLOT_CONFIG}
          style={{ width: "100%", height: "100%" }}
          useResizeHandler
        />
      </div>
      {loading ? (
        <div className="mt-3 flex items-center gap-2 text-xs text-slate-400">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-sky-400 border-t-transparent" />
          Updating dispatch…
        </div>
      ) : null}
    </div>
  );
}
