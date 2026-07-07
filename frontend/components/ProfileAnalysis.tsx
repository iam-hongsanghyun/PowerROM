"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { LoadDurationCurveChart } from "@/components/charts/LoadDurationCurveChart";
import {
  calculateSystem,
  type CalculateResponse,
  type Capacities,
  type DispatchMode,
  type DispatchResponse,
  type GeneratorKey,
  type Shares,
} from "@/lib/api";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

// ─── Constants ────────────────────────────────────────────────────────────────

const CARBON_SCENARIOS = [0, 50, 100, 200] as const;
const ESS_SCENARIOS = [80, 150, 280, 450] as const; // $/kWh

const SCENARIO_COLORS: Record<number, string> = {
  0: "#94a3b8",    // slate-400
  50: "#60a5fa",   // blue-400
  100: "#f59e0b",  // amber-400
  200: "#ef4444",  // red-400
};

const ESS_SCENARIO_COLORS: Record<number, string> = {
  80: "#10b981",   // emerald — future target
  150: "#60a5fa",  // blue
  280: "#f59e0b",  // amber — today ~default
  450: "#ef4444",  // red — high cost
};

const PLOT_BASE: Partial<Plotly.Layout> = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(248,250,252,1)",
  margin: { l: 58, r: 24, b: 44, t: 8 },
  font: { family: "inherit", size: 11, color: "#64748b" },
  legend: {
    x: 0.02,
    y: 0.98,
    bgcolor: "rgba(255,255,255,0.85)",
    bordercolor: "#e2e8f0",
    borderwidth: 1,
    font: { size: 10 },
  },
  hovermode: "closest",
};

const PLOT_CONFIG: Partial<Plotly.Config> = {
  responsive: true,
  displayModeBar: false,
};

// ─── Types ───────────────────────────────────────────────────────────────────

interface Props {
  result: CalculateResponse | null;
  dispatchResult: DispatchResponse | null;
  isDispatchLoading: boolean;
  country: string;
  carbonPrice: number;
  essCostUsdKwh: number;
  shares: Shares;
  capacities: Capacities;
  annualDemandTwh: number;
  evPenetration: number;
  dispatchMode: DispatchMode;
  weatherYears: number[];
  generatorOrder: GeneratorKey[];
}

// ─── Helper ──────────────────────────────────────────────────────────────────

function vertLine(xPct: number): Partial<Plotly.Shape> {
  return {
    type: "line",
    xref: "x",
    yref: "paper",
    x0: xPct,
    x1: xPct,
    y0: 0,
    y1: 1,
    line: { color: "#0ea5e9", width: 1.5, dash: "dot" },
  };
}

function SummaryCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-[0.14em] text-slate-400">{label}</div>
      <div className="mt-1.5 text-xl font-semibold text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

function numericBreakdownValue(result: CalculateResponse, generator: string, key: string): number {
  const value = result.lcoe_by_generator[generator]?.[key];
  return typeof value === "number" ? value : 0;
}

function ChartCard({ title, subtitle, children }: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.35)]">
      <div className="mb-3">
        <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
        <p className="text-xs text-slate-400">{subtitle}</p>
      </div>
      <div style={{ height: 280 }}>
        {children}
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function ProfileAnalysis({
  result,
  dispatchResult,
  isDispatchLoading,
  country,
  carbonPrice,
  essCostUsdKwh,
  shares,
  capacities,
  annualDemandTwh,
  evPenetration,
  dispatchMode,
  weatherYears,
  generatorOrder,
}: Props) {
  // Carbon-price sensitivity curves (4 fixed carbon prices × current ESS cost)
  const [carbonCurves, setCarbonCurves] = useState<Record<number, CalculateResponse>>({});
  // ESS cost sensitivity curves (4 fixed ESS costs × current carbon price)
  const [essCurves, setEssCurves] = useState<Record<number, CalculateResponse>>({});
  const [loadingScenarios, setLoadingScenarios] = useState(false);
  const lastFetchKey = useRef<string>("");

  const fetchKey = `${country}|${annualDemandTwh}|${evPenetration}|${essCostUsdKwh}|${dispatchMode}|${weatherYears.join(",")}|${JSON.stringify(capacities)}|${generatorOrder.join(",")}`;

  useEffect(() => {
    if (!result) return;
    if (lastFetchKey.current === fetchKey) return;
    lastFetchKey.current = fetchKey;

    setLoadingScenarios(true);

    const carbonFetches = CARBON_SCENARIOS.map((price) =>
      calculateSystem({
        country,
        capacities_gw: capacities,
        carbon_price: price,
        ev_penetration: evPenetration,
        annual_demand_twh: annualDemandTwh,
        custom_params: { ess: { short_dur: { capex_usd_kwh: essCostUsdKwh } } },
        dispatch_mode: dispatchMode,
        weather_years: weatherYears.length ? weatherYears : null,
        generator_order: generatorOrder,
      }),
    );

    const essFetches = ESS_SCENARIOS.map((cost) =>
      calculateSystem({
        country,
        capacities_gw: capacities,
        carbon_price: carbonPrice,
        ev_penetration: evPenetration,
        annual_demand_twh: annualDemandTwh,
        custom_params: { ess: { short_dur: { capex_usd_kwh: cost } } },
        dispatch_mode: dispatchMode,
        weather_years: weatherYears.length ? weatherYears : null,
        generator_order: generatorOrder,
      }),
    );

    Promise.all([Promise.all(carbonFetches), Promise.all(essFetches)])
      .then(([carbonResponses, essResponses]) => {
        const cmap: Record<number, CalculateResponse> = {};
        CARBON_SCENARIOS.forEach((price, i) => { cmap[price] = carbonResponses[i]!; });
        setCarbonCurves(cmap);

        const emap: Record<number, CalculateResponse> = {};
        ESS_SCENARIOS.forEach((cost, i) => { emap[cost] = essResponses[i]!; });
        setEssCurves(emap);
      })
      .catch(console.error)
      .finally(() => setLoadingScenarios(false));
  }, [fetchKey, result, country, capacities, annualDemandTwh, evPenetration, carbonPrice, essCostUsdKwh, dispatchMode, weatherYears, generatorOrder]);

  if (!result) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-slate-400">
        Waiting for first calculation…
      </div>
    );
  }

  const curve = result.curve_data;
  const vrePct = curve.map((p) => p.vre_share * 100);

  // Current scenario VRE position
  const currentVrePct = (shares.solar + shares.wind_onshore) * 100;
  const servedVrePct = (
    numericBreakdownValue(result, "solar", "realized_share")
    + numericBreakdownValue(result, "wind_onshore", "realized_share")
  ) * 100;
  const currentIdx = vrePct.reduce((best, v, i) =>
    Math.abs(v - currentVrePct) < Math.abs(vrePct[best]! - currentVrePct) ? i : best,
    0,
  );
  const cp = curve[currentIdx]!; // current curve point

  // Min-LCOE point
  const minLcoe = Math.min(...curve.map((p) => p.system_lcoe));
  const minIdx = curve.findIndex((p) => p.system_lcoe === minLcoe);
  const minVrePct = vrePct[minIdx]!;

  // ─── Chart 1: Cost composition stacked area ─────────────────────────────────
  // Derive O&M = system_lcoe - (capex + fuel + carbon + integration + ess)
  const omValues = curve.map(
    (p) => Math.max(0, p.system_lcoe - p.capex - p.fuel - p.carbon - p.integration - p.ess),
  );

  const stackLayers: Array<{ label: string; color: string; values: number[] }> = [
    { label: "CAPEX", color: "#3b82f6", values: curve.map((p) => p.capex) },
    { label: "O&M", color: "#64748b", values: omValues },
    { label: "Fuel", color: "#f59e0b", values: curve.map((p) => p.fuel) },
    { label: "Carbon", color: "#ef4444", values: curve.map((p) => p.carbon) },
    { label: "Integration", color: "#f97316", values: curve.map((p) => p.integration) },
    { label: "Storage", color: "#8b5cf6", values: curve.map((p) => p.ess) },
  ];

  const stackTraces: Plotly.Data[] = stackLayers.map(({ label, color, values }) => ({
    type: "scatter" as const,
    mode: "lines" as const,
    name: label,
    x: vrePct,
    y: values,
    stackgroup: "cost",
    fillcolor: color + "cc",
    line: { color, width: 0.5 },
    hovertemplate: `${label}: $%{y:.1f}/MWh<extra></extra>`,
  }));

  // Secondary Y-axis: curtailment rate
  const curtailmentTrace: Plotly.Data = {
    type: "scatter" as const,
    mode: "lines" as const,
    name: "Curtailment Rate",
    x: vrePct,
    y: curve.map((p) => p.curtailment_rate * 100),
    yaxis: "y2",
    line: { color: "#f97316", width: 1.5, dash: "dash" as const },
    hovertemplate: "Curtailment: %{y:.1f}%<extra></extra>",
  };

  const chart1Layout: Partial<Plotly.Layout> = {
    ...PLOT_BASE,
    xaxis: { title: { text: "VRE Share (%)" }, gridcolor: "#e2e8f0", range: [0, 100] },
    yaxis: { title: { text: "LCOE ($/MWh)" }, gridcolor: "#e2e8f0" },
    yaxis2: {
      title: { text: "Curtailment (%)" },
      overlaying: "y",
      side: "right",
      gridcolor: "rgba(0,0,0,0)",
      range: [0, 100],
    },
    shapes: [
      vertLine(currentVrePct),
      {
        type: "line",
        xref: "x",
        yref: "paper",
        x0: minVrePct,
        x1: minVrePct,
        y0: 0,
        y1: 1,
        line: { color: "#10b981", width: 1.5, dash: "dash" },
      },
    ],
    annotations: [
      {
        x: currentVrePct,
        y: 1,
        xref: "x",
        yref: "paper",
        text: "Current",
        showarrow: false,
        font: { size: 9, color: "#0ea5e9" },
        xanchor: "left",
        yanchor: "top",
      },
      {
        x: minVrePct,
        y: 1,
        xref: "x",
        yref: "paper",
        text: "Min LCOE",
        showarrow: false,
        font: { size: 9, color: "#10b981" },
        xanchor: "left",
        yanchor: "top",
      },
    ],
  };

  // ─── Chart 2: Carbon price sensitivity ──────────────────────────────────────
  const sensitivityTraces: Plotly.Data[] = CARBON_SCENARIOS.map((price) => {
    const sc = carbonCurves[price];
    const isCurrent = price === carbonPrice;
    return {
      type: "scatter" as const,
      mode: "lines" as const,
      name: `$${price}/tCO₂`,
      x: sc ? sc.curve_data.map((p) => p.vre_share * 100) : [],
      y: sc ? sc.curve_data.map((p) => p.system_lcoe) : [],
      line: {
        color: SCENARIO_COLORS[price],
        width: isCurrent ? 2.5 : 1.5,
        dash: isCurrent ? "solid" : "dot",
      },
      hovertemplate: `$${price}/tCO₂: $%{y:.1f}/MWh @ %{x:.0f}% VRE<extra></extra>`,
    };
  });

  // Current-scenario star on sensitivity chart
  const currentScenarioCurve = carbonCurves[carbonPrice];
  const sensitivityCurrentY = currentScenarioCurve
    ? currentScenarioCurve.curve_data[currentIdx]?.system_lcoe
    : cp.system_lcoe;

  sensitivityTraces.push({
    type: "scatter" as const,
    mode: "markers" as const,
    name: "Current scenario",
    x: [currentVrePct],
    y: [sensitivityCurrentY ?? cp.system_lcoe],
    marker: { color: "#0ea5e9", size: 10, symbol: "diamond" as const },
    showlegend: false,
    hovertemplate: `Current: $%{y:.1f}/MWh<extra></extra>`,
  });

  const chart2Layout: Partial<Plotly.Layout> = {
    ...PLOT_BASE,
    xaxis: { title: { text: "VRE Share (%)" }, gridcolor: "#e2e8f0", range: [0, 100] },
    yaxis: { title: { text: "LCOE ($/MWh)" }, gridcolor: "#e2e8f0" },
    shapes: [vertLine(currentVrePct)],
  };

  // ─── Chart 3: Cost–Emissions Pareto frontier ─────────────────────────────────
  // X = emission intensity (gCO2/kWh), Y = LCOE, coloured by carbon price
  // Reversed X so "better" (lower emissions) is to the right — decarbonisation direction
  const frontierTraces: Plotly.Data[] = CARBON_SCENARIOS.map((price) => {
    const sc = carbonCurves[price];
    const isCurrent = price === carbonPrice;
    // Annotate VRE% on hover
    const hoverTexts = sc
      ? sc.curve_data.map(
          (p) =>
            `$${price}/tCO₂ — VRE: ${(p.vre_share * 100).toFixed(0)}%<br>Emissions: ${(p.emission_intensity * 1000).toFixed(0)} gCO₂/kWh<br>LCOE: $${p.system_lcoe.toFixed(1)}/MWh`,
        )
      : [];
    return {
      type: "scatter" as const,
      mode: "lines" as const,
      name: `$${price}/tCO₂`,
      x: sc ? sc.curve_data.map((p) => p.emission_intensity * 1000) : [],
      y: sc ? sc.curve_data.map((p) => p.system_lcoe) : [],
      text: hoverTexts,
      hovertemplate: "%{text}<extra></extra>",
      line: {
        color: SCENARIO_COLORS[price],
        width: isCurrent ? 2.5 : 1.5,
        dash: isCurrent ? "solid" : "dot",
      },
    };
  });

  // Current-scenario star on Pareto
  frontierTraces.push({
    type: "scatter" as const,
    mode: "markers" as const,
    name: "Current",
    x: [cp.emission_intensity * 1000],
    y: [cp.system_lcoe],
    marker: {
      color: "#0ea5e9",
      size: 12,
      symbol: "star" as const,
      line: { color: "#0369a1", width: 1 },
    },
    hovertemplate: `Current mix<br>${(cp.emission_intensity * 1000).toFixed(0)} gCO₂/kWh — $${cp.system_lcoe.toFixed(1)}/MWh<extra></extra>`,
  });

  const chart3Layout: Partial<Plotly.Layout> = {
    ...PLOT_BASE,
    xaxis: {
      title: { text: "Emission Intensity (gCO₂/kWh)" },
      gridcolor: "#e2e8f0",
      autorange: "reversed" as const,
    },
    yaxis: { title: { text: "LCOE ($/MWh)" }, gridcolor: "#e2e8f0" },
    annotations: [
      {
        x: 10,
        y: 1,
        xref: "paper" as const,
        yref: "paper" as const,
        text: "← lower emissions →",
        showarrow: false,
        font: { size: 9, color: "#94a3b8" },
        xanchor: "right",
      },
    ],
  };

  // ─── Chart 4: ESS cost sensitivity ──────────────────────────────────────────
  // Shows how system LCOE changes at different battery cost assumptions.
  const essTraces: Plotly.Data[] = ESS_SCENARIOS.map((cost) => {
    const sc = essCurves[cost];
    const isCurrent = cost === essCostUsdKwh;
    return {
      type: "scatter" as const,
      mode: "lines" as const,
      name: `$${cost}/kWh`,
      x: sc ? sc.curve_data.map((p) => p.vre_share * 100) : [],
      y: sc ? sc.curve_data.map((p) => p.system_lcoe) : [],
      line: {
        color: ESS_SCENARIO_COLORS[cost],
        width: isCurrent ? 2.5 : 1.5,
        dash: isCurrent ? "solid" : "dot",
      },
      hovertemplate: `$${cost}/kWh: $%{y:.1f}/MWh @ %{x:.0f}% VRE<extra></extra>`,
    };
  });

  // Add ESS-only contribution trace (how much cost is just from storage)
  const essOnlyTrace: Plotly.Data = {
    type: "scatter" as const,
    mode: "lines" as const,
    name: "ESS cost only (current)",
    x: vrePct,
    y: curve.map((p) => p.ess),
    line: { color: "#8b5cf6", width: 1.5, dash: "dashdot" as const },
    hovertemplate: "ESS cost: $%{y:.2f}/MWh @ %{x:.0f}% VRE<extra></extra>",
  };

  // Current-scenario diamond
  essTraces.push({
    type: "scatter" as const,
    mode: "markers" as const,
    name: "Current",
    x: [currentVrePct],
    y: [cp.system_lcoe],
    marker: { color: "#0ea5e9", size: 10, symbol: "diamond" as const },
    showlegend: false,
    hovertemplate: `Current: $%{y:.1f}/MWh<extra></extra>`,
  });

  const chart4Layout: Partial<Plotly.Layout> = {
    ...PLOT_BASE,
    xaxis: { title: { text: "VRE Share (%)" }, gridcolor: "#e2e8f0", range: [0, 100] },
    yaxis: { title: { text: "System LCOE ($/MWh)" }, gridcolor: "#e2e8f0" },
    shapes: [vertLine(currentVrePct)],
    annotations: [
      {
        x: 0.98,
        y: 0.98,
        xref: "paper" as const,
        yref: "paper" as const,
        text: `Current: $${essCostUsdKwh}/kWh`,
        showarrow: false,
        font: { size: 9, color: "#64748b" },
        xanchor: "right",
        yanchor: "top",
        bgcolor: "rgba(255,255,255,0.8)",
        bordercolor: "#e2e8f0",
        borderwidth: 1,
      },
    ],
  };

  // ─── Chart 5: ESS split (short + long duration) ─────────────────────────────
  const essShortTrace: Plotly.Data = {
    type: "scatter" as const,
    mode: "lines" as const,
    name: "Short-duration ESS",
    x: vrePct,
    y: curve.map((p) => p.ess_short_gwh),
    stackgroup: "ess",
    fillcolor: "#3b82f6cc",
    line: { color: "#3b82f6", width: 0.5 },
    hovertemplate: "Short ESS: %{y:.0f} GWh @ %{x:.0f}% VRE<extra></extra>",
  };

  const essLongTrace: Plotly.Data = {
    type: "scatter" as const,
    mode: "lines" as const,
    name: "Long-duration ESS",
    x: vrePct,
    y: curve.map((p) => p.ess_long_gwh),
    stackgroup: "ess",
    fillcolor: "#8b5cf6cc",
    line: { color: "#8b5cf6", width: 0.5 },
    hovertemplate: "Long ESS: %{y:.0f} GWh @ %{x:.0f}% VRE<extra></extra>",
  };

  const chart5Layout: Partial<Plotly.Layout> = {
    ...PLOT_BASE,
    xaxis: { title: { text: "VRE Share (%)" }, gridcolor: "#e2e8f0", range: [0, 100] },
    yaxis: { title: { text: "Storage Capacity (GWh)" }, gridcolor: "#e2e8f0" },
    shapes: [
      vertLine(currentVrePct),
      {
        type: "line",
        xref: "x",
        yref: "paper",
        x0: 65,
        x1: 65,
        y0: 0,
        y1: 1,
        line: { color: "#8b5cf6", width: 1.5, dash: "dash" },
      },
    ],
    annotations: [
      {
        x: 65,
        y: 1,
        xref: "x",
        yref: "paper",
        text: "65% VRE (long-dur threshold)",
        showarrow: false,
        font: { size: 9, color: "#8b5cf6" },
        xanchor: "left",
        yanchor: "top",
      },
    ],
  };

  // ─── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      <LoadDurationCurveChart
        ldc={dispatchResult?.ldc ?? result.ldc ?? null}
        dispatch={dispatchResult?.dispatch ?? result.dispatch ?? null}
        loading={isDispatchLoading}
      />

      {/* Summary strip */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <SummaryCard
          label="VRE Capacity Share"
          value={`${currentVrePct.toFixed(0)}%`}
          sub={`Solar ${capacities.solar.toFixed(0)} GW + Wind ${capacities.wind_onshore.toFixed(0)} GW`}
        />
        <SummaryCard
          label="VRE Served Share"
          value={`${servedVrePct.toFixed(0)}%`}
          sub="Share of annual served generation"
        />
        <SummaryCard
          label="System LCOE"
          value={`$${result.system_lcoe.toFixed(1)}/MWh`}
          sub={`Annual cost $${result.annual_system_cost_usd_billion.toFixed(1)}B/yr`}
        />
        <SummaryCard
          label="Emission Intensity"
          value={`${(result.emission_intensity * 1000).toFixed(0)} gCO₂/kWh`}
          sub={`${result.annual_emissions_mtco2.toFixed(1)} MtCO₂/yr`}
        />
        <SummaryCard
          label="Storage at Current VRE"
          value={`${result.ess_requirement_gwh.toFixed(0)} GWh`}
          sub={`${result.ess_requirement_gw.toFixed(1)} GW · $${essCostUsdKwh}/kWh assumed`}
        />
      </div>

      {loadingScenarios && (
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-sky-400 border-t-transparent" />
          Loading sensitivity scenarios…
        </div>
      )}

      {/* 2×2 charts */}
      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="Cost Composition vs VRE Share"
          subtitle="How CAPEX, fuel, carbon, integration and storage costs evolve as renewables grow (dashed = curtailment %)"
        >
          <Plot
            data={[...stackTraces, curtailmentTrace]}
            layout={chart1Layout}
            config={PLOT_CONFIG}
            style={{ width: "100%", height: "100%" }}
            useResizeHandler
          />
        </ChartCard>

        <ChartCard
          title="Carbon Price Sensitivity"
          subtitle={`LCOE at 4 carbon price levels — current is $${carbonPrice}/tCO₂ (solid line)`}
        >
          <Plot
            data={sensitivityTraces}
            layout={chart2Layout}
            config={PLOT_CONFIG}
            style={{ width: "100%", height: "100%" }}
            useResizeHandler
          />
        </ChartCard>

        <ChartCard
          title="Cost–Emissions Frontier"
          subtitle="Each curve traces cost vs emissions as VRE share increases (x-axis flipped: left = more emissions)"
        >
          <Plot
            data={frontierTraces}
            layout={chart3Layout}
            config={PLOT_CONFIG}
            style={{ width: "100%", height: "100%" }}
            useResizeHandler
          />
        </ChartCard>

        <ChartCard
          title="Battery Cost Sensitivity"
          subtitle={`How system LCOE shifts at different ESS CAPEX — current $${essCostUsdKwh}/kWh (solid); dashed line = ESS cost only`}
        >
          <Plot
            data={[...essTraces, essOnlyTrace]}
            layout={chart4Layout}
            config={PLOT_CONFIG}
            style={{ width: "100%", height: "100%" }}
            useResizeHandler
          />
        </ChartCard>
      </div>

      {/* Chart 5: ESS split — full width */}
      <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.35)]">
        <div className="mb-3">
          <h3 className="text-sm font-semibold text-slate-800">Storage Capacity Split: Short- vs Long-Duration</h3>
          <p className="text-xs text-slate-400">Stacked GWh required by storage tier vs VRE share — long-duration kicks in above 65% VRE (purple dashed line)</p>
        </div>
        <div style={{ height: 280 }}>
          <Plot
            data={[essShortTrace, essLongTrace]}
            layout={chart5Layout}
            config={PLOT_CONFIG}
            style={{ width: "100%", height: "100%" }}
            useResizeHandler
          />
        </div>
      </div>
    </div>
  );
}
